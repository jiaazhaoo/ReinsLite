"""The ops panel (board #/ops): the delivery batches -- production and rework, the batches whose results go to a client --
laid out as a schedule, with what the automation did to them and how far the people reviewing them are.

    timeline     each batch's stages: done (start..end), running (start..now, then a forecast to its end), planned
                 (forecast from how long the same stage took per case in earlier delivery batches of the project);
                 what it is waiting for when it is not moving (spend approval, preflight, a resource, a pause reason)
    automation   what happened to these batches without a person (watchdog, sentinel, supervisor, repairs) and what a
                 session did, newest first; the machine's timers and when each runs next
    review       per batch: cases that need a person (results outside [results].good), how many are assigned, how many
                 have a verdict, the share judged wrong, and a link to the review page serving the batch

Read-only. Project words stay in reins.toml: [results] (as for /production) and, optionally,

    [board]
    review_url = "http://127.0.0.1:8773/?batch={batch}"   # fallback link; a live registered service whose data
                                                         # covers the batch's work_dir wins
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import subprocess
from pathlib import Path

from . import production
from .paths import work_dir

TYPES = production.TYPES                      # ("production", "rework"): what is delivered
LIVE = ("open", "running", "paused")
RECENT_DAYS = 3                               # finished delivery batches stay on the schedule this long
HISTORY_DAYS = 60                             # per-case stage durations are learnt from this window
EVENT_HOURS = 72


def _t(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s[:19])
    except ValueError:
        return None


def _iso(d: dt.datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def stage_rates(con, project: str) -> dict[str, float]:
    """stage -> median seconds per case in finished delivery batches of the project (the forecast's yardstick)."""
    since = _iso(dt.datetime.now() - dt.timedelta(days=HISTORY_DAYS))
    per: dict[str, list[float]] = {}
    for st, started, ended, n in con.execute(
            "SELECT s.stage, s.started, s.ended, b.n_cases FROM batch_stage s JOIN batch b USING (batch_id) "
            "WHERE b.project=? AND b.type IN (%s) AND s.status='done' AND s.started IS NOT NULL AND s.ended IS NOT NULL "
            "AND s.ended > ? AND b.n_cases > 0" % ",".join("?" * len(TYPES)), (project, *TYPES, since)):
        a, e = _t(started), _t(ended)
        if a and e and e > a:
            per.setdefault(st, []).append((e - a).total_seconds() / n)
    return {st: statistics.median(v) for st, v in per.items() if v}


def _done_counts(con, bid: str) -> dict[str, int]:
    """stage -> cases whose newest event there is done or skipped."""
    out: dict[str, int] = {}
    for st, n in con.execute(
            "SELECT stage, COUNT(*) FROM (SELECT stage, case_id, status, MAX(id) FROM case_event WHERE batch_id=? "
            "GROUP BY stage, case_id) WHERE status IN ('done','skipped') GROUP BY stage", (bid,)):
        out[st] = n
    return out


def _waiting(con, b, stages: list[dict]) -> str | None:
    """Why a live batch is not moving, in plain words (None when it is)."""
    if b["status"] == "paused":
        return "paused: " + (b["status_reason"] or "no reason recorded")
    if b["status"] == "open":
        if any(s["paid"] for s in stages) and not (b["spend_cap"] or 0) > 0:
            return "waiting for a spend cap (paid stages)"
        if not b["preflight_ok"]:
            return "waiting for preflight"
        return "opened, not started"
    w = con.execute("SELECT rule, mem_gb, gpu_gb FROM resource_claim WHERE batch_id=? AND state='waiting' "
                    "ORDER BY id LIMIT 1", (b["batch_id"],)).fetchone()
    if w:
        need = ", ".join(x for x in (f"{w['gpu_gb']:g} GB GPU" if w["gpu_gb"] else "",
                                     f"{w['mem_gb']:g} GB RAM" if w["mem_gb"] else "") if x)
        return f"waiting for the machine: {w['rule']}" + (f" ({need})" if need else "")
    if b["status"] == "running" and not any(s["status"] == "running" for s in stages):
        return "running, no stage started"
    return None


def _schedule(con, b, rates: dict[str, float], now: dt.datetime) -> dict:
    n = b["n_cases"] or 0
    done = _done_counts(con, b["batch_id"])
    stages, segs = [], []
    cursor = now
    eta_known = True
    for s in con.execute("SELECT * FROM batch_stage WHERE batch_id=? ORDER BY ord", (b["batch_id"],)):
        a, e = _t(s["started"]), _t(s["ended"])
        d = done.get(s["stage"], 0)
        item = {"stage": s["stage"], "status": s["status"], "paid": bool(s["paid"]), "done": d, "of": n,
                "started": s["started"], "ended": s["ended"], "version": s["module_version"]}
        stages.append(item)
        if s["status"] in ("done", "failed", "skipped") and a:
            segs.append({"stage": s["stage"], "kind": s["status"], "from": _iso(a), "to": _iso(e or a)})
        elif s["status"] == "running" and a and b["status"] != "running":
            # the stage was left open but the batch is not running (paused, process gone): not moving, no forecast
            segs.append({"stage": s["stage"], "kind": "stalled", "from": _iso(a), "to": _iso(now)})
            eta_known = False
        elif s["status"] == "running" and a:
            segs.append({"stage": s["stage"], "kind": "running", "from": _iso(a), "to": _iso(now)})
            left = max(n - d, 0)
            elapsed = (now - a).total_seconds()
            per = elapsed / d if d >= 3 else rates.get(s["stage"])           # this run's own pace once it has one
            if per:
                cursor = now + dt.timedelta(seconds=per * left)
                segs.append({"stage": s["stage"], "kind": "forecast", "from": _iso(now), "to": _iso(cursor)})
            else:
                eta_known = False
        elif s["status"] == "planned":
            per = rates.get(s["stage"])
            if per and n:
                start = cursor
                cursor = cursor + dt.timedelta(seconds=per * n)
                segs.append({"stage": s["stage"], "kind": "planned", "from": _iso(start), "to": _iso(cursor)})
            else:
                eta_known = False
    live = b["status"] in LIVE
    blocked = _waiting(con, b, stages) if live else None
    finish = None
    if live and eta_known and segs and not (blocked and not blocked.startswith("waiting for the machine")):
        finish = _iso(cursor)
    return {"stages": stages, "segments": segs, "waiting": blocked, "forecast_finish": finish}


def _review(con, b, cfg: dict) -> dict:
    """Cases needing a person, assigned, judged, wrong share; link to the review page."""
    spec = cfg.get("results") or {}
    good = set(spec.get("good") or [])
    cases = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (b["batch_id"],))}
    need = None
    wd = work_dir(b) if b["work_dir"] else None
    if spec.get("file") and wd:
        res = production._results(Path(wd) / spec["file"], spec)
        need = sum(1 for c in cases if c in res and res[c][0] not in good)
    assigned = con.execute("SELECT COUNT(DISTINCT case_id) FROM review_assignment WHERE batch_id=?",
                           (b["batch_id"],)).fetchone()[0]
    verdicts = {}
    if cases:
        rows = con.execute("SELECT case_id, verdict, reviewer, MAX(id), at FROM review_event WHERE action='verdict' "
                           "AND batch_id=? GROUP BY case_id", (b["batch_id"],)).fetchall()
        verdicts = {r["case_id"]: r for r in rows}
    ours = {c: r for c, r in verdicts.items() if not str(r["reviewer"]).startswith("client:")}
    client = {c: r for c, r in verdicts.items() if str(r["reviewer"]).startswith("client:")}
    wrong = sum(1 for r in ours.values() if r["verdict"] == "wrong")
    return {"need_person": need, "assigned": assigned, "judged": len(ours), "wrong": wrong,
            "wrong_share": round(wrong / len(ours), 3) if ours else None,
            "reviewers": sorted({r["reviewer"] for r in ours.values()}),
            "last": max((r["at"] for r in ours.values()), default=None),
            # what the client said back (feedback imported as client:<who> verdicts), kept apart from our reviewers
            "client_judged": len(client), "client_wrong": sum(1 for r in client.values() if r["verdict"] == "wrong"),
            "url": _review_url(con, b, wd, cfg)}


def _has(con, table: str) -> bool:
    """Tables made on first use (service, deadline) are absent from a fresh registry."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _review_url(con, b, wd: str | None, cfg: dict) -> str | None:
    if wd and _has(con, "service"):
        for s in con.execute("SELECT host, port, data FROM service WHERE status='live' AND port IS NOT NULL"):
            try:
                dirs = json.loads(s["data"] or "[]")
            except ValueError:
                dirs = []
            if any(str(d).split(" ")[0].rstrip("/") in (wd.rstrip("/"),) or str(d).startswith(wd.rstrip("/") + "/")
                   for d in dirs):
                return f"http://{s['host'] or '127.0.0.1'}:{s['port']}/"
    tpl = (cfg.get("board") or {}).get("review_url")
    return tpl.format(batch=b["batch_id"], work_dir=wd or "", scope=b["scope"]) if tpl else None


def _events(con, ids: list[str], since: str) -> list[dict]:
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = con.execute(f"SELECT at, batch_id, event, detail, session FROM batch_event WHERE batch_id IN ({q}) "
                       f"AND at > ? ORDER BY id DESC LIMIT 400", (*ids, since)).fetchall()
    return [{"at": r["at"], "batch": r["batch_id"], "event": r["event"], "detail": (r["detail"] or "")[:300],
             "by": r["session"] or "automation"} for r in rows]


def _notices(con, ids: list[str], since: str) -> list[dict]:
    """Alerts about these batches, and machine-wide ones (a balance, the gateway) -- not alerts about other batches."""
    import re
    other = re.compile(r"[a-z0-9]+(?:-[a-z0-9_]+)*-\d{8}-\d+")                 # a batch id
    out = []
    for r in con.execute("SELECT at, level, title, body FROM notification WHERE at > ? AND level IN ('warn','action') "
                         "ORDER BY id DESC LIMIT 200", (since,)):
        text = (r["title"] or "") + " " + (r["body"] or "")
        hit = next((i for i in ids if i in text), None)
        if hit or (r["level"] == "action" and not other.search(text)):
            out.append({"at": r["at"], "level": r["level"], "title": r["title"], "body": (r["body"] or "")[:300],
                        "batch": hit})
    return out


def timers() -> list[dict]:
    """The machine's systemd user timers: what runs by itself, last and next run."""
    try:
        p = subprocess.run(["systemctl", "--user", "list-timers", "--all", "--output=json"], capture_output=True,
                           text=True, timeout=10)
        rows = json.loads(p.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    us = lambda v: _iso(dt.datetime.fromtimestamp(v / 1e6)) if v else None      # noqa: E731
    out = []
    for r in rows:
        unit = r.get("activates") or r.get("unit")
        desc, frag = "", ""
        try:
            desc, frag = (subprocess.run(["systemctl", "--user", "show", "-p", "Description", "-p", "FragmentPath",
                                          "--value", unit], capture_output=True, text=True, timeout=5)
                          .stdout.strip().split("\n") + ["", ""])[:2]
        except (OSError, subprocess.SubprocessError):
            pass
        if not frag.startswith(str(Path.home())):          # timers the user installed, not the distribution's
            continue
        out.append({"unit": unit, "about": desc, "next": us(r.get("next")), "last": us(r.get("last"))})
    return sorted(out, key=lambda x: x["next"] or "9999")


def view(con) -> dict:
    now = dt.datetime.now()
    recent = _iso(now - dt.timedelta(days=RECENT_DAYS))
    q = ",".join("?" * len(TYPES))
    rows = con.execute(f"SELECT * FROM batch WHERE type IN ({q}) AND (status IN ('open','running','paused') "
                       f"OR coalesce(closed, created) > ?) ORDER BY created", (*TYPES, recent)).fetchall()
    rows = [b for b in rows if not production._abandoned(con, b["batch_id"])]
    deadlines = {r["scope"]: r["due"] for r in con.execute("SELECT scope, due FROM deadline")} if _has(con, "deadline") else {}
    spend = {r[0]: r[1] for r in con.execute("SELECT batch_id, SUM(amount) FROM spend GROUP BY batch_id")}
    rates_by_project: dict[str, dict] = {}
    batches = []
    for b in rows:
        cfg = production._cfg(b["project"])
        rates = rates_by_project.setdefault(b["project"], stage_rates(con, b["project"]))
        sch = _schedule(con, b, rates, now)
        due = deadlines.get(b["scope"])
        late = bool(due and sch["forecast_finish"] and sch["forecast_finish"] > due)
        batches.append({"batch_id": b["batch_id"], "project": b["project"], "scope": b["scope"], "type": b["type"],
                        "purpose": b["purpose"], "status": b["status"], "n_cases": b["n_cases"],
                        "owner": b["owner_session"], "created": b["created"], "closed": b["closed"],
                        "spend": round(spend.get(b["batch_id"], 0) or 0, 2), "spend_cap": b["spend_cap"],
                        "deadline": due, "late": late, **sch, "review": _review(con, b, cfg)})
    ids = [b["batch_id"] for b in batches]
    since = _iso(now - dt.timedelta(hours=EVENT_HOURS))
    return {"now": _iso(now), "batches": batches, "events": _events(con, ids, since),
            "notices": _notices(con, ids, since), "timers": timers()}
