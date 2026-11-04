"""A batch that stops is known at once, and one killed for memory goes on by itself (user 2026-11-04).

2026-11-04 04:53 the memory watchdog killed one process of braintree-wp1-production-20261103-2; reins paused the batch
and wrote a notice to the board. Nobody saw it until 09:00: the owner session's waiter waited for an *end*, the paused
(SIGSTOPped) processes still counted as alive, and nothing reached a phone. Two watchdog passes:

left_running   every batch that left 'running' for paused / failed since the last pass (a state event) -> one action
               notice (board + notify_cmd, e.g. a phone) and a message to the owner session, unless the owner itself
               asked for it. (`reins wait` now also returns on a pause.)
auto_resume    a batch paused because a process was killed goes on by itself once memory is back, if its project allows
               it in reins.toml:

                   [resume]
                   after_kill = true      # the command redoes an unfinished step itself (it is resumable)
                   max = 3                # restarts per batch in 24 h before it stays paused and asks a person
                   wait_min = 10          # minutes after the kill before the first try
                   mem_free_pct = 40      # and at least this share of RAM available

               The restart is the batch's last command, same cwd, started as its owner (stop what is left, then
               `reins run` it again). Every restart is a batch event and a message to the owner; after `max` the
               batch stays paused with one action notice.
"""
from __future__ import annotations

import datetime as dt
import os
import shlex
from contextlib import contextmanager
from pathlib import Path

from . import batches, inbox, notify, resources
from .store import home, now, tx

LEFT = ("paused", "failed")


def _mark(name: str) -> Path:
    return home() / name


def left_running(con) -> list[str]:
    f = _mark("last_left_running_event")
    try:
        last = int(f.read_text().strip())
    except (OSError, ValueError):
        last = con.execute("SELECT COALESCE(MAX(id), 0) FROM batch_event").fetchone()[0]   # first pass: nothing old is new
        f.write_text(str(last))
        return []
    rows = con.execute("SELECT e.id, e.batch_id, e.detail, e.session, b.owner_session, b.status_reason, b.type FROM batch_event e"
                       " JOIN batch b ON b.batch_id=e.batch_id WHERE e.id>? AND e.event='state' AND e.detail LIKE 'batch running -> %'"
                       " ORDER BY e.id", (last,)).fetchall()
    out = []
    for r in rows:
        last = r["id"]
        to = r["detail"].split("->", 1)[1].split()[0].strip()
        if to not in LEFT:
            continue
        own = r["owner_session"] or ""
        if own and r["session"] and r["session"] == own and not os.environ.get("REINS_SUPERVISOR"):
            continue                                                     # the owner did it itself: it knows
        why = r["status_reason"] or r["detail"]
        nxt = (f"it goes on by itself once memory is back ([resume] after_kill)" if _resumable(con, r["batch_id"])
               and (r["status_reason"] or "").startswith(resources.KILLED) else f"reins find {r['batch_id']}")
        title = f"{r['batch_id']} stopped ({to})"
        body = f"{why[:400]}\nnext: {nxt}"
        notify.send(con, f"left-running:{r['batch_id']}:{r['id']}", "action", title, body, cooldown_min=0)
        if own:
            try:
                inbox.send(con, own, title, body, batch=r["batch_id"])
            except Exception:                                            # noqa: BLE001 -- never stop the watchdog
                pass
        out.append(f"{r['batch_id']}: left running -> {to}")
    f.write_text(str(last))
    return out


def _cfg(con, bid: str) -> dict:
    from . import production
    return (production._cfg(batches.get(con, bid)["project"]).get("resume") or {})


def _resumable(con, bid: str) -> bool:
    try:
        return bool(_cfg(con, bid).get("after_kill"))
    except Exception:                                                    # noqa: BLE001
        return False


def _mem_free_pct() -> float:
    try:
        kv = {l.split(":")[0]: float(l.split()[1]) for l in open("/proc/meminfo") if l.split()[0].rstrip(":") in ("MemTotal", "MemAvailable")}
        return 100 * kv["MemAvailable"] / kv["MemTotal"]
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return 0.0


@contextmanager
def _as(sid: str | None):
    old = os.environ.get("REINS_SESSION")
    if sid:
        os.environ["REINS_SESSION"] = sid
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("REINS_SESSION", None)
        else:
            os.environ["REINS_SESSION"] = old


def auto_resume(con, mem_free_pct: float | None = None) -> list[str]:
    from . import runner
    out = []
    for b in con.execute("SELECT * FROM batch WHERE status='paused' AND status_reason LIKE ?", (resources.KILLED + "%",)).fetchall():
        bid = b["batch_id"]
        try:
            cfg = _cfg(con, bid)
        except Exception:                                                # noqa: BLE001
            continue
        if not cfg.get("after_kill"):
            continue
        kill = con.execute("SELECT id, at FROM batch_event WHERE batch_id=? AND event='killed' ORDER BY id DESC LIMIT 1", (bid,)).fetchone()
        if not kill:
            continue
        if dt.datetime.now() - dt.datetime.fromisoformat(kill["at"]) < dt.timedelta(minutes=float(cfg.get("wait_min", 10))):
            continue
        free = _mem_free_pct() if mem_free_pct is None else mem_free_pct
        if free < float(cfg.get("mem_free_pct", 40)):
            continue
        day = (dt.datetime.now() - dt.timedelta(hours=24)).isoformat(timespec="seconds")      # restarts in the last 24 h
        tries = con.execute("SELECT COUNT(*) FROM batch_event WHERE batch_id=? AND event='auto_resume' AND at>?", (bid, day)).fetchone()[0]
        mx = int(cfg.get("max", 3))
        if tries >= mx:
            notify.send(con, f"auto-resume-gave-up:{bid}:{kill['id']}", "action", f"{bid}: killed again, not restarted",
                        f"{mx} automatic restarts since the last start; it stays paused. Last: {b['status_reason']}", cooldown_min=0)
            continue
        p = con.execute("SELECT * FROM process WHERE batch_id=? ORDER BY id DESC LIMIT 1", (bid,)).fetchone()
        if not p:
            continue
        owner = b["owner_session"]
        try:
            with _as(owner):
                runner.ctl(con, "stop", bid, "auto resume: clear what is left of the killed run", system=True)
                with tx(con):
                    batches._event(con, bid, "auto_resume", f"try {tries + 1}/{mx}: {free:.0f}% RAM free; restart: {p['cmd'][:300]}")
                mv = None if p["stage"] == runner.SELF else (con.execute(
                    "SELECT module_version FROM batch_stage WHERE batch_id=? AND stage=?", (bid, p["stage"])).fetchone() or [None])[0]
                r = runner.start(con, bid, p["stage"], shlex.split(p["cmd"]), Path(p["cwd"]), mv)
        except Exception as e:                                           # noqa: BLE001
            notify.send(con, f"auto-resume-failed:{bid}:{kill['id']}", "action", f"{bid}: automatic restart failed", str(e)[:500],
                        cooldown_min=0)
            out.append(f"{bid}: auto resume failed: {e}")
            continue
        msg = f"restarted after the kill ({tries + 1}/{mx}, {free:.0f}% RAM free); log {r.get('log')}"
        notify.send(con, f"auto-resume:{bid}:{kill['id']}", "info", f"{bid} restarted by itself", msg, cooldown_min=0)
        if owner:
            try:
                inbox.send(con, owner, f"{bid} restarted by itself", msg, batch=bid)
            except Exception:                                            # noqa: BLE001
                pass
        out.append(f"{bid}: auto resumed ({tries + 1}/{mx})")
    return out
