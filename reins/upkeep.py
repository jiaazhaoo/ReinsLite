"""Keeping the registry true over months, run by the watchdog (reins/watchdog.py) on every pass:

    backup        once a day a consistent copy of reins.db (sqlite backup API), checked, kept N days, on another
                  disk when [backup] dir says so; a failed or missing backup is an action notice
    reconcile     a batch marked running with nothing of it alive and no progress becomes paused, with the reason
                  ("process gone: ...") and one notice; it is never left to look busy (37 did on 2026-10-19)
    leases        a lease on a finished batch, or on the worktree of a released / abandoned candidate, is released
    disk          a warning at [disk_warn_pct] before the pause at disk_pause_pct

    reins backup [--now]        make one now (also prints where the last one is)

config.toml:
    [backup]
    dir = "/other/disk/reins-backups"     # default $REINS_HOME/backups (same disk: protects against corruption only)
    keep_days = 14
    disk_warn_pct = 85                    # top level, next to disk_pause_pct
"""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import time
from pathlib import Path

from . import batches, notify
from .store import config, home, now, tx

GONE_AFTER_MIN = 30          # a batch run by reins: nothing alive and no progress for this long
GONE_AFTER_H_UNRUN = 24      # a batch reins never started (stages reported from outside): no progress for this long


def _cfg() -> dict:
    c = config()
    return {"dir": str(home() / "backups"), "keep_days": 14, **(c.get("backup") or {})}


def last_backup() -> Path | None:
    d = Path(_cfg()["dir"])
    files = sorted(d.glob("reins-*.db")) if d.is_dir() else []
    return files[-1] if files else None


def backup(con, force: bool = False) -> list[str]:
    """One consistent copy a day (SQLite's online backup: safe while the gateway and the runner write)."""
    c = _cfg()
    d = Path(c["dir"])
    last = last_backup()
    if not force and last and time.time() - last.stat().st_mtime < 23 * 3600:
        return []
    try:
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"reins-{dt.datetime.now():%Y%m%d-%H%M}.db"
        tmp = dest.with_suffix(".part")
        out = sqlite3.connect(tmp)
        try:
            con.backup(out)
            ok = out.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            out.close()
        if ok != "ok":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"the copy failed its check: {ok}")
        tmp.replace(dest)
    except Exception as e:                                                      # noqa: BLE001
        notify.send(con, "backup_failed", "action", "registry backup failed", f"{e} (dir {d})", cooldown_min=360)
        return [f"backup failed: {e}"]
    cutoff = time.time() - float(c["keep_days"]) * 86400
    for f in d.glob("reins-*.db"):
        if f != dest and f.stat().st_mtime < cutoff:
            f.unlink()
    same_disk = Path(d).resolve().stat().st_dev == home().resolve().stat().st_dev
    if same_disk:
        notify.send(con, "backup_same_disk", "warn", "registry backups are on the registry's own disk",
                    f"{d}: a disk failure loses both; set [backup] dir in {home() / 'config.toml'} to another disk",
                    cooldown_min=7 * 24 * 60)
    return [f"backup {dest} ({dest.stat().st_size // 1024} KB)"]


def _boot_time() -> str | None:
    try:
        for line in open("/proc/stat"):
            if line.startswith("btime "):
                return dt.datetime.fromtimestamp(int(line.split()[1])).isoformat(timespec="seconds")
    except OSError:
        pass
    return None


def _newest_output(con, bid: str) -> float | None:
    from .watchdog import _newest_mtime
    ts = [_newest_mtime(r[0]) for r in con.execute("SELECT output_path FROM batch_stage WHERE batch_id=? AND status='running'"
                                                   " AND output_path IS NOT NULL", (bid,))]
    ts = [t for t in ts if t]
    return max(ts) if ts else None


def reconcile(con) -> list[str]:
    """Running batches with nothing alive and no progress -> paused, with what became of the process."""
    from . import runner
    out = []
    boot = _boot_time()
    for b in con.execute("SELECT * FROM batch WHERE status='running'").fetchall():
        bid = b["batch_id"]
        procs = con.execute("SELECT * FROM process WHERE batch_id=? ORDER BY id", (bid,)).fetchall()
        if any(p["state"] in ("running", "paused") and (runner._alive(p["pgid"]) or runner._alive(p["supervisor_pid"]))
               for p in procs):
            continue
        signs = [con.execute("SELECT MAX(at) FROM case_event WHERE batch_id=?", (bid,)).fetchone()[0],
                 con.execute("SELECT MAX(COALESCE(ended, started)) FROM process WHERE batch_id=?", (bid,)).fetchone()[0],
                 b["created"]]
        mt = _newest_output(con, bid)
        if mt:
            signs.append(dt.datetime.fromtimestamp(mt).isoformat(timespec="seconds"))
        quiet_min = (dt.datetime.now() - dt.datetime.fromisoformat(max(s for s in signs if s))).total_seconds() / 60
        if quiet_min < (GONE_AFTER_MIN if procs else GONE_AFTER_H_UNRUN * 60):
            continue
        last = procs[-1] if procs else None
        if not last:
            why = f"process gone: nothing run by reins and no progress for {quiet_min / 60:.0f} h"
        elif boot and last["started"] < boot and last["state"] in ("running", "lost"):
            why = f"process gone: the machine restarted at {boot[:16].replace('T', ' ')}"
        elif last["state"] in ("running", "lost"):
            why = "process gone: lost (process and supervisor ended without a word)"
        elif last["state"] == "exited" and last["exit_code"] == 0:
            why = "process gone: it ended normally (exit 0) but its stages were never closed"
        elif last["state"] == "exited":
            why = f"process gone: it failed (exit {last['exit_code']})"
        else:
            why = f"process gone: {last['state']}"
        with tx(con):
            for p in procs:
                if p["state"] in ("running", "paused"):
                    con.execute("UPDATE process SET state='lost', ended=COALESCE(ended, ?) WHERE id=?", (now(), p["id"]))
        batches.set_status(con, bid, "paused", why, system=True)
        out.append(f"{bid}: {why} -> paused")
    if 0 < len(out) <= 3:                                # one notice each; a backlog (after a reboot) is one notice
        for line in out:
            bid, why = line.split(": ", 1)
            notify.send(con, f"gone:{bid}", "action", f"{bid}: {why.removesuffix(' -> paused')}",
                        ("check its results, then end its stages or reins batch abandon" if "exit 0" in why else
                         "start it again with reins run, or reins batch abandon if it will not be finished"), cooldown_min=0)
    elif out:
        notify.send(con, f"gone:{now()[:16]}", "action", f"{len(out)} batches paused: their process is gone",
                    "the board lists them under 'needs you' with the command for each", cooldown_min=0)
    return out


def release_leases(con) -> list[str]:
    """Leases nobody can need: on a finished batch, or on the worktree of a candidate that was released or abandoned."""
    gone = []
    for r in con.execute("SELECT l.resource FROM lease l JOIN batch b ON l.resource = 'batch:' || b.batch_id"
                         " WHERE b.status IN ('done', 'failed', 'closed')"):
        gone.append(r[0])
    for r in con.execute("SELECT l.resource FROM lease l JOIN module_version v ON l.resource = 'worktree:' || v.worktree"
                         " WHERE v.status IN ('released', 'retired', 'abandoned')"):
        gone.append(r[0])
    for r in con.execute("SELECT resource FROM lease WHERE resource LIKE 'worktree:%'"):
        if not Path(r[0].split(":", 1)[1]).exists():
            gone.append(r[0])
    gone = sorted(set(gone))
    if gone:
        with tx(con):
            con.executemany("DELETE FROM lease WHERE resource=?", [(g,) for g in gone])
    return [f"released {len(gone)} lease(s) nobody can need"] if gone else []


def disk_warning(con) -> list[str]:
    c = config()
    warn = float(c.get("disk_warn_pct", 85))
    out, seen = [], set()
    from .store import disks
    for path in disks() + [str(home())]:
        if Path(path).exists() and Path(path).stat().st_dev not in seen:      # one disk, one notice
            seen.add(Path(path).stat().st_dev)
            u = shutil.disk_usage(path)
            pct = 100 * u.used / u.total
            if warn <= pct < c["disk_pause_pct"] and notify.send(
                    con, f"disk_warn:{path}", "warn", f"disk {path} at {pct:.0f}%",
                    f"{u.free / 1e9:.0f} GB free; batches pause at {c['disk_pause_pct']}%", cooldown_min=24 * 60):
                out.append(f"disk {path} {pct:.0f}% (warn)")                  # logged when told, not every minute
    return out


def run(con) -> list[str]:
    return backup(con) + reconcile(con) + release_leases(con) + disk_warning(con)
