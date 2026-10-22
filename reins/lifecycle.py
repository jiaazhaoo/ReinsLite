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
