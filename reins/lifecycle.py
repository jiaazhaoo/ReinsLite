"""A batch reaches an end. Regression for 2026-10-22: 68 batches were left open, every owner session ended or idle.

What kept them open, from the owners' transcripts and the ledger:
  - 39 ran a script with `reins run B STAGE -- CMD` that exited 0 but reported no per-case outcome (an experiment, a
    profile, a ceiling test). stage_end refuses a stage while any case has no outcome, so the stage stayed running for
    ever; 72 "cannot end" notices went to no one (no notify channel) and only 1 of 33 owners ever tried to close.
  - 9 were paused by the disk guard (all running batches, alive or not) and nothing resumed them when space came back.
  - every owner moved on after reporting results; nothing asked it to close what it opened.

Rules (finish() on a process's exit, sweep() on every watchdog pass):
  - a non-production batch whose command exited 0 and that reported no case outcome at all: its open stages end as
    'done (no per-case outcomes reported)'; the batch becomes done. Production and rework still need every case.
  - production / rework with nothing alive: settled by reins/lineage.py (results complete -> done; covered by a later
    run -> closed as superseded; idle stale_days -> done partial or closed stopped). The person only starts runs.
  - disk pauses: only batches with something alive are paused; when the disk is below the pause line again they are
    resumed (their processes were only stopped).
  - a non-production batch with nothing alive and no sign of life for STALE_DAYS is closed as abandoned (outputs kept).
  - a session about to stop that owns batches with nothing alive is asked once what to do with them (hooks).
"""
from __future__ import annotations

import datetime as dt

from . import batches, notify
from .store import config, now, tx

STRICT = batches.STRICT_TYPES
STALE_DAYS = 7


def _alive(con, bid: str) -> bool:
    from . import runner
    return any(p["state"] in ("running", "paused") and (runner._alive(p["pgid"]) or runner._alive(p["supervisor_pid"]))
               for p in con.execute("SELECT * FROM process WHERE batch_id=?", (bid,)).fetchall())


def _outcomes(con, bid: str) -> int:
    return con.execute("SELECT COUNT(*) FROM case_event WHERE batch_id=? AND status<>'started'", (bid,)).fetchone()[0]


def finish_unreported(con, bid: str, why: str = "its command exited 0") -> bool:
    """End the open stages of a non-production batch that reported no case outcome; True when the batch is done."""
    b = batches.get(con, bid)
    if b["type"] in STRICT or _outcomes(con, bid) or b["status"] in ("done", "closed", "failed"):
        return False
    t = now()
    with tx(con):
        open_ = con.execute("SELECT stage, status FROM batch_stage WHERE batch_id=? AND status IN ('running','planned')",
                            (bid,)).fetchall()
        for st in open_:
            new = "done" if st["status"] == "running" else "skipped"
            con.execute("UPDATE batch_stage SET status=?, ended=?, note=? WHERE batch_id=? AND stage=?",
                        (new, t, f"no per-case outcomes reported; ended because {why}", bid, st["stage"]))
            batches._event(con, bid, "stage_end" if new == "done" else "stage_skipped",
                           f"{st['stage']}: no per-case outcomes reported ({why})")
        if b["status"] == "paused":
            con.execute("UPDATE batch SET status='running', status_reason=NULL WHERE batch_id=?", (bid,))
        batches._maybe_done(con, bid)
    return batches.get(con, bid)["status"] == "done"


def on_exit_ok(con, bid: str) -> str:
    """A batch's command exited 0 with stages still open (runner). Returns what happened."""
    if finish_unreported(con, bid):
        return "done"
    b = batches.get(con, bid)
    if b["status"] in ("done", "closed"):
        return b["status"]
    from . import inbox
    inbox.to_batch_owner(con, bid, f"{bid}: its command ended (exit 0) but stages are open",
                         "check the results, then end the stages (reins batch stage-end ...) or reins batch abandon B --why")
    return "ended_open"


def resume_after_disk(con) -> list[str]:
    """Batches the disk guard paused, resumed once every watched disk is below the pause line again."""
    import shutil
    from . import runner
    from .store import disks, home
    cfg = config()
    worst = max(100 * shutil.disk_usage(p).used / shutil.disk_usage(p).total for p in dict.fromkeys(disks() + [str(home())]))
    if worst >= cfg["disk_pause_pct"] - 2:                  # a little below the line, or it flaps
        return []
    out = []
    for b in con.execute("SELECT batch_id FROM batch WHERE status='paused' AND status_reason LIKE 'disk %'").fetchall():
        if _alive(con, b[0]):
            runner.ctl(con, "resume", b[0], f"disk back to {worst:.0f}%", system=True)
            out.append(f"{b[0]}: resumed (disk {worst:.0f}%)")
    return out


def _idle_days(con, bid: str) -> float | None:
    h = batches.idle_hours(con, bid)
    return None if h is None else h / 24


def sweep(con, stale_days: float | None = None) -> list[str]:
    """Every pass: finish what ended unreported, resume disk pauses, close forgotten non-production batches."""
    stale_days = float(config().get("stale_days", STALE_DAYS) if stale_days is None else stale_days)
    out = resume_after_disk(con)
    closed = []
    for b in con.execute("SELECT * FROM batch WHERE status IN ('running','paused','open')").fetchall():
        bid = b["batch_id"]
        if _alive(con, bid):
            continue
        last = con.execute("SELECT state, exit_code FROM process WHERE batch_id=? ORDER BY id DESC LIMIT 1", (bid,)).fetchone()
        if last and last["state"] in ("exited", "lost") and last["exit_code"] == 0 and finish_unreported(con, bid):
            out.append(f"{bid}: done (exit 0, no per-case outcomes)")
            continue
        if b["type"] in STRICT:                              # production / rework: by its results and its later runs
            from . import lineage
            r = lineage.settle(con, bid, stale_days)
            if r:
                out.append(r)
            continue
        idle = _idle_days(con, bid)
        if idle is not None and idle >= stale_days:
            why = f"nothing alive and no sign of life for {idle:.0f} days (closed by the watchdog; outputs kept)"
            with tx(con):
                con.execute("UPDATE batch SET status='closed', status_reason=?, closed=? WHERE batch_id=?",
                            (f"abandoned: {why}", now(), bid))
                batches._event(con, bid, "abandoned", why)
                batches._event(con, bid, "closed", "stale")
                con.execute("DELETE FROM lease WHERE resource=?", (f"batch:{bid}",))
            closed.append(bid)
    for f in (tend_candidates, tend_issues):              # development work reaches an end the same way
        try:
            out += f(con)
        except Exception as e:                                                  # noqa: BLE001
            out.append(f"{f.__name__}: {e}")
    if closed:
        notify.send(con, f"stale_closed:{now()[:13]}", "info", f"{len(closed)} forgotten batches closed",
                    f"no process, no progress for {stale_days:g}+ days; outputs kept: {', '.join(closed[:20])}", cooldown_min=0)
        out.append(f"closed {len(closed)} stale batch(es)")
    return out


def loose_for(con, sid: str) -> list[dict]:
    """Batches this session owns with nothing alive and not finished: what it should close or hand over."""
    rows = []
    for b in con.execute("SELECT batch_id, type, status, status_reason FROM batch WHERE owner_session=?"
                         " AND status IN ('open','running','paused')", (sid,)).fetchall():
        if not _alive(con, b["batch_id"]):
            rows.append(dict(b))
    return rows


# ------------------------------------------------------------------ candidates and issues reach an end too
CANDIDATE_IDLE_DAYS = 7
VERIFY_WINDOW_DAYS = 14


def _days_since(ts: str | None) -> float | None:
    return None if not ts else (dt.datetime.now() - dt.datetime.fromisoformat(ts[:19])).total_seconds() / 86400


def _candidate_last_activity(con, v) -> str | None:
    """The newest sign of work on a candidate: an edit in its worktree, a module event, its creation."""
    ts = [v["created"],
          con.execute("SELECT MAX(at) FROM module_event WHERE version=?", (v["version"],)).fetchone()[0],
          con.execute("SELECT MAX(at) FROM session_event WHERE kind='edit' AND worktree=?", (v["worktree"],)).fetchone()[0]
          if v["worktree"] else None]
    return max(t for t in ts if t)


def tend_candidates(con, idle_days: float | None = None) -> list[str]:
    """A candidate nobody has touched for idle_days (and whose session is not active) is abandoned; its branch and
    worktree stay, `reins dev adopt WORKTREE` takes it up again. Issues it had taken go back to open.
    2026-10-22: 6 candidates from 2026-10-09..22 sat open, their sessions ended or idle."""
    idle_days = float(config().get("candidate_idle_days", CANDIDATE_IDLE_DAYS) if idle_days is None else idle_days)
    from . import dev, issues
    out = []
    for v in con.execute("SELECT * FROM module_version WHERE status='candidate'").fetchall():
        s = con.execute("SELECT status FROM session WHERE id=?", (v["session"],)).fetchone()
        if s and s["status"] == "active":
            continue
        days = _days_since(_candidate_last_activity(con, v))
        if days is None or days < idle_days:
            continue
        why = (f"nothing done on it for {days:.0f} days and its session is {s['status'] if s else 'gone'}; "
               f"branch {v['branch'] or '?'} and worktree kept (reins dev adopt {v['worktree']} to take it up again)")
        dev.abandon(con, v["version"], why)
        issues.ensure(con)
        with tx(con):
            for i in con.execute("SELECT id FROM issue WHERE version=? AND status='in_progress'", (v["version"],)).fetchall():
                con.execute("UPDATE issue SET status='open', version=NULL, fix_session=NULL WHERE id=?", (i[0],))
                issues._ev(con, i[0], "reopened", f"{v['version']} abandoned: {why[:120]}")
        out.append(f"{v['version']}: abandoned ({days:.0f} days idle)")
    return out


def tend_issues(con, window_days: float | None = None) -> list[str]:
    """Issues that can no longer change: on a batch that was closed without finishing, or on a retired service ->
    closed; fixed issues whose cases have a result in a run after the fix -> verified; fixed with no such run within
    window_days -> closed, saying so."""
    window_days = float(config().get("verify_window_days", VERIFY_WINDOW_DAYS) if window_days is None else window_days)
    import json as _json
    from . import issues
    issues.ensure(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(issue)")}
    out = []
    for i in con.execute("SELECT * FROM issue WHERE status IN ('open','in_progress','fixed')").fetchall():
        if i["batch_id"] and i["status"] == "open":
            b = con.execute("SELECT status, status_reason FROM batch WHERE batch_id=?", (i["batch_id"],)).fetchone()
            if b and b["status"] == "closed" and (b["status_reason"] or "").startswith("abandoned"):
                issues.close(con, i["id"], f"its batch {i['batch_id']} was closed without finishing ({(b['status_reason'] or '')[11:90]})")
                out.append(f"issue #{i['id']}: closed (batch closed)")
                continue
        if "service" in cols and i["service"] and i["status"] == "open":
            s = con.execute("SELECT status FROM service WHERE name=?", (i["service"],)).fetchone()
            if s and s["status"] == "retired":
                issues.close(con, i["id"], f"service {i['service']} retired")
                out.append(f"issue #{i['id']}: closed (service retired)")
                continue
        if i["status"] != "fixed":
            continue
        fixed_at = con.execute("SELECT MAX(at) FROM issue_event WHERE issue_id=? AND event='fixed'", (i["id"],)).fetchone()[0]
        cases = _json.loads(i["cases"] or "[]")
        if cases and fixed_at:
            q = ",".join("?" * len(cases))
            good = con.execute(f"SELECT COUNT(DISTINCT case_id) FROM case_current WHERE case_id IN ({q}) AND at>? AND status='done'"
                               + (" AND stage=?" if i["stage"] else ""), (*cases, fixed_at, *([i["stage"]] if i["stage"] else []))).fetchone()[0]
            if good >= len(cases):
                issues.verify(con, i["id"], "reins", f"all {len(cases)} cases done in a run after the fix ({i['version']})")
                out.append(f"issue #{i['id']}: verified")
                continue
        days = _days_since(fixed_at)
        if days is not None and days >= window_days:
            issues.close(con, i["id"], f"fixed by {i['version']} {days:.0f} days ago; its cases were not run again to verify")
            out.append(f"issue #{i['id']}: closed (fixed, never re-run)")
    return out
