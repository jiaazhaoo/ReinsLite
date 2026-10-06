"""C8 watchdog: a process independent of any session that judges progress, not liveness.

    reins watchdog once          one pass, prints findings
    reins watchdog run           loop (systemd unit in systemd/reins-watchdog.service)

Checks, per running batch: a stage whose outputs stopped growing; cases past their stage's time limit; a process
group that vanished; the paid gateway down while a paid stage runs. Per machine: disk, memory, GPU.
Actions are only the safe ones: notify, pause (SIGSTOP + batch paused). It never edits code, rules or data.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from . import batches, notify, runner
from .store import config, connect, home


def _minutes_since(ts: str | None) -> float | None:
    if not ts:
        return None
    return (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() / 60


def _newest_mtime(path: str | None) -> float | None:
    if not path or not Path(path).exists():
        return None
    p = Path(path)
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    return max((f.stat().st_mtime for f in files), default=None)


def check_batches(con, cfg) -> list[str]:
    out = []
    for b in con.execute("SELECT * FROM batch WHERE status='running'"):
        bid = b["batch_id"]
        for p in runner.live_processes(con, bid):
            if p["state"] == "running" and not runner._alive(p["pgid"]):
                sup_alive = runner._alive(p["supervisor_pid"])
                if not sup_alive:
                    con.execute("UPDATE process SET state='lost', ended=? WHERE id=?", (dt.datetime.now().isoformat(timespec="seconds"), p["id"]))
                    notify.send(con, f"lost:{bid}:{p['stage']}", "action", f"{bid}/{p['stage']}: process and supervisor gone",
                                f"last log: {p['log_path']}\nreins run {bid} {p['stage']} -- ... to continue", cooldown_min=30)
                    out.append(f"{bid}: process lost")
        for st in con.execute("SELECT * FROM batch_stage WHERE batch_id=? AND status='running'", (bid,)):
            last_ev = con.execute("SELECT MAX(at) FROM case_event WHERE batch_id=? AND stage=?", (bid, st["stage"])).fetchone()[0]
            m_ev = _minutes_since(last_ev)
            mt = _newest_mtime(st["output_path"]) or _newest_mtime(st["log_path"])
            m_out = (time.time() - mt) / 60 if mt else None
            m_start = _minutes_since(st["started"])
            recent = [m for m in (m_ev, m_out) if m is not None]
            quiet = min(recent) if recent else m_start
            if quiet is not None and quiet > cfg["stall_minutes"]:
                notify.send(con, f"stall:{bid}:{st['stage']}", "warn",
                            f"{bid}/{st['stage']}: no progress for {quiet:.0f} min",
                            f"no new case outcome or output file. log: {st['log_path']}", cooldown_min=60)
                out.append(f"{bid}/{st['stage']}: stalled {quiet:.0f} min")
            if st["time_limit_s"]:
                late = [r[0] for r in con.execute(
                    "SELECT oachargeid FROM case_current WHERE batch_id=? AND stage=? AND status='started' AND at < ?",
                    (bid, st["stage"], (dt.datetime.now() - dt.timedelta(seconds=st["time_limit_s"])).isoformat(timespec="seconds")))]
                if late:
                    notify.send(con, f"late:{bid}:{st['stage']}", "warn",
                                f"{bid}/{st['stage']}: {len(late)} case(s) past the {st['time_limit_s']} s limit",
                                ", ".join(late[:10]), cooldown_min=30)
                    out.append(f"{bid}/{st['stage']}: {len(late)} late cases")
            if st["paid"]:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{cfg['gateway_port']}/health", timeout=3)
                except Exception:                                               # noqa: BLE001
                    notify.send(con, "gateway_down", "action", "paid gateway is not running",
                                f"{bid}/{st['stage']} needs it: reins gateway serve", cooldown_min=30)
                    out.append("gateway down")
    return out


def _pause_all(con, why: str) -> list[str]:
    paused = []
    for b in con.execute("SELECT batch_id FROM batch WHERE status='running'").fetchall():
        runner.ctl(con, "pause", b[0], why, system=True)
        paused.append(b[0])
    return paused


def check_machine(con, cfg) -> list[str]:
    out = []
    for path in {"/data", str(home())}:
        if Path(path).exists():
            u = shutil.disk_usage(path)
            pct = 100 * u.used / u.total
            if pct >= cfg["disk_pause_pct"]:
                paused = _pause_all(con, f"disk {path} at {pct:.0f}%")
                notify.send(con, f"disk:{path}", "action", f"disk {path} at {pct:.0f}%: batches paused",
                            f"paused: {', '.join(paused) or 'none running'}. Free space, then reins ctl resume", cooldown_min=60)
                out.append(f"disk {path} {pct:.0f}%")
    try:
        mem = dict(l.split(":") for l in Path("/proc/meminfo").read_text().splitlines())
        total = int(mem["MemTotal"].split()[0]); avail = int(mem["MemAvailable"].split()[0])
        pct = 100 * (1 - avail / total)
        if pct >= cfg["mem_pause_pct"]:
            newest = con.execute("SELECT batch_id FROM batch WHERE status='running' ORDER BY created DESC LIMIT 1").fetchone()
            if newest:
                runner.ctl(con, "pause", newest[0], f"memory at {pct:.0f}%", system=True)
            notify.send(con, "memory", "action", f"memory at {pct:.0f}%",
                        f"paused newest batch {newest[0] if newest else '(none)'}; reduce workers, then resume", cooldown_min=15)
            out.append(f"memory {pct:.0f}%")
    except (OSError, KeyError, ValueError):
        pass
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        for i, line in enumerate(q.stdout.strip().splitlines()):
            used, total = (int(x) for x in line.split(","))
            pct = 100 * used / total
            if pct >= cfg["gpu_warn_pct"]:
                notify.send(con, f"gpu{i}", "warn", f"GPU {i} memory at {pct:.0f}%",
                            "the system watchdog kills at 92%; do not start another GPU job", cooldown_min=30)
                out.append(f"gpu{i} {pct:.0f}%")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return out


def once(con=None) -> list[str]:
    con = con or connect()
    cfg = config()
    return check_batches(con, cfg) + check_machine(con, cfg)


def run(interval: int = 60) -> None:
    con = connect()
    while True:
        try:
            for f in once(con):
                print(f"{dt.datetime.now().isoformat(timespec='seconds')} {f}", flush=True)
        except Exception as e:                                                  # noqa: BLE001
            print(f"watchdog error: {e}", flush=True)
        time.sleep(interval)
