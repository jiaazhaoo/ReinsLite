"""C8 watchdog: a process independent of any session that judges progress, not liveness.

    reins watchdog once          one pass, prints findings
    reins watchdog run           loop (systemd unit in systemd/reins-watchdog.service)

Checks, per running batch: a stage whose outputs stopped growing; cases past their stage's time limit; a process
group that vanished; the paid gateway down while a paid stage runs. Per machine: disk, memory, GPU.
Actions are only the safe ones: notify, pause (SIGSTOP + batch paused). It never edits code, rules or data.
Resource claims (reins/resources.py) whose process is gone are released here too, in case their supervisor died.
Upkeep (reins/upkeep.py) runs on the same pass: a daily registry backup, running batches whose process is gone become
paused (first, so they raise no stall notices), leases nobody can need, an early disk warning.
"""
from __future__ import annotations

import datetime as dt
import os
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
    from .paths import resolve
    path = resolve(path)                                  # a stage's recorded output may have been moved since
    if not path or not Path(path).exists():
        return None
    p = Path(path)
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    return max((f.stat().st_mtime for f in files), default=None)


def check_batches(con, cfg) -> list[str]:
    out = []
    for b in con.execute("SELECT * FROM batch WHERE status='running'"):
        bid = b["batch_id"]
        if b["time_budget_h"]:
            used = batches.elapsed_h(con, bid)
            if used and used > b["time_budget_h"]:
                if cfg.get("overtime_action") == "pause" and runner.live_processes(con, bid):
                    runner.ctl(con, "pause", bid, f"over its time budget: {used:.1f} h of {b['time_budget_h']:g} h", system=True)
                    notify.send(con, f"overtime:{bid}", "action", f"{bid} paused: {used:.1f} h of a {b['time_budget_h']:g} h budget",
                                "resume (reins ctl resume) to let it finish, raise the budget, or reins ctl stop", cooldown_min=0)
                else:
                    notify.send(con, f"overtime:{bid}", "warn", f"{bid}: {used:.1f} h used of a {b['time_budget_h']:g} h budget",
                                "decide: let it finish, or reins ctl stop", cooldown_min=120, backoff=True)
                out.append(f"{bid}: over its time budget")
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
            never_run = (st["note"] or "").startswith("adopted") and not con.execute(
                "SELECT 1 FROM process WHERE batch_id=? LIMIT 1", (bid,)).fetchone()
            if never_run:
                continue                                 # adopted, never run by reins: it can neither see nor act on it
            if quiet is not None and quiet > cfg["stall_minutes"]:
                notify.send(con, f"stall:{bid}:{st['stage']}", "warn",
                            f"{bid}/{st['stage']}: no progress for {quiet:.0f} min",
                            f"no new case outcome or output file. log: {st['log_path']}", cooldown_min=60, backoff=True)
                out.append(f"{bid}/{st['stage']}: stalled {quiet:.0f} min")
            if st["time_limit_s"]:
                late = [r[0] for r in con.execute(
                    "SELECT case_id FROM case_current WHERE batch_id=? AND stage=? AND status='started' AND at < ?",
                    (bid, st["stage"], (dt.datetime.now() - dt.timedelta(seconds=st["time_limit_s"])).isoformat(timespec="seconds")))]
                if late:
                    notify.send(con, f"late:{bid}:{st['stage']}", "warn",
                                f"{bid}/{st['stage']}: {len(late)} case(s) past the {st['time_limit_s']} s limit",
                                ", ".join(late[:10]), cooldown_min=30, backoff=True)
                    out.append(f"{bid}/{st['stage']}: {len(late)} late cases")
            if st["paid"]:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{cfg['gateway_port']}/health", timeout=3)
                except Exception:                                               # noqa: BLE001
                    notify.send(con, "gateway_down", "action", "paid gateway is not running",
                                f"{bid}/{st['stage']} needs it: reins gateway serve", cooldown_min=30, backoff=True)
                    out.append("gateway down")
    return out


def _pause_all(con, why: str) -> list[str]:
    """Pause what is running on a full disk -- only batches with something alive (a dead one writes nothing; pausing it
    left it paused for ever). They are resumed when the disk is below the line again (reins/lifecycle.py)."""
    from . import lifecycle
    paused = []
    for b in con.execute("SELECT batch_id FROM batch WHERE status='running'").fetchall():
        if lifecycle._alive(con, b[0]):
            runner.ctl(con, "pause", b[0], why, system=True)
            paused.append(b[0])
    return paused


def check_machine(con, cfg) -> list[str]:
    out = []
    from .store import disks
    for path in dict.fromkeys(disks() + [str(home())]):
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


_last_poll = 0.0


def poll_balances_due(con, cfg) -> list[str]:
    """Provider balances every balance_poll_min minutes (free endpoints); a drop without gateway spend is flagged."""
    global _last_poll
    if time.time() - _last_poll < cfg["balance_poll_min"] * 60:
        return []
    _last_poll = time.time()
    from . import spend
    out = []
    for r in spend.poll_balances(con):
        out.append(f"{r['provider']} balance {r['balance']}")
        from . import providers
        low = float((providers.by_ledger(r["provider"]) or {}).get("low_balance", 5))
        if r["balance"] is not None and r["balance"] < low:
            notify.send(con, f"balance_low:{r['provider']}", "action", f"{r['provider']} balance ${r['balance']:.2f}",
                        "top up before the next paid stage; the gateway will pause batches at the cap", cooldown_min=360)
    return out


def once(con=None) -> list[str]:
    con = con or connect()
    cfg = config()
    from . import resources
    n = resources.cleanup(con)                                  # claims of processes that are gone
    from . import sentinel, upkeep
    return ([f"released {n} resource claim(s) of ended processes"] if n else []) + upkeep.reconcile(con) + check_batches(con, cfg) + \
        check_machine(con, cfg) + poll_balances_due(con, cfg) + release_stale_reservations(con, cfg) + sentinel.watch_running(con) + \
        _lifecycle(con) + upkeep.backup(con) + upkeep.release_leases(con) + upkeep.disk_warning(con) + _services(con) + check_gateway(con, cfg) + _sync(con) + \
        (check_code_fresh(con, cfg) if os.environ.get("INVOCATION_ID") else []) + _invariants(con) + _legacy(con) + _loop(con) + _quality(con)   # only the systemd watchdog restarts units


def _quality(con) -> list[str]:
    """Each release's real error rate from human and client verdicts (reins/quality.py), daily."""
    from . import quality
    try:
        return quality.tend(con)
    except Exception as e:                                                      # noqa: BLE001 -- never stop the watchdog
        return [f"quality skipped: {e}"]


def _loop(con) -> list[str]:
    """The improvement loop (reins/loop.py): reviewer verdicts in, misses out as issues, hourly."""
    from . import loop
    try:
        return loop.tend(con)
    except Exception as e:                                                      # noqa: BLE001 -- never stop the watchdog
        return [f"improvement loop skipped: {e}"]


def _legacy(con) -> list[str]:
    """Legacy commands (cli.LEGACY) unused for 14 days after they were marked: one issue each, to remove the code."""
    import datetime as _dt
    from . import cli, issues
    try:
        if _dt.date.today() < _dt.date.fromisoformat(cli.LEGACY_SINCE) + _dt.timedelta(days=14):
            return []
        since = (_dt.datetime.now() - _dt.timedelta(days=14)).isoformat(timespec="seconds")
        used = {r[0] for r in con.execute("SELECT DISTINCT command FROM legacy_use WHERE at>?", (since,))}
        out = []
        for c in sorted(cli.LEGACY - used):
            symptom = f"legacy command 'reins {c}' unused for 14 days: remove it (cli.LEGACY)"
            if con.execute("SELECT 1 FROM issue WHERE symptom=?", (symptom,)).fetchone():
                continue
            try:
                n = issues.open_(con, None, [], symptom, module="core")
            except Exception:                                                   # noqa: BLE001 -- no core module here
                continue
            out.append(f"issue #{n}: remove legacy command {c}")
        return out
    except Exception as e:                                                      # noqa: BLE001 -- never stop the watchdog
        return [f"legacy check skipped: {e}"]


def _invariants(con) -> list[str]:
    """The registry's state checked and repaired every pass (reins/invariants.py). What the repairs cannot clear becomes
    an issue for a development session; nothing goes to the user's board."""
    from . import invariants
    try:
        r = invariants.tend(con)
    except Exception as e:                                                      # noqa: BLE001 -- never stop the watchdog
        return [f"state check skipped: {e}"]
    out = [f"state check repaired: {x}" for x in r["repaired"][:20]]
    if r["issues"]:
        out.append(f"state check opened issue(s) {r['issues']}")
    return out


def _sync(con) -> list[str]:
    from . import dev
    from . import weights_store
    out = []
    for f in (dev.sync_all, weights_store.push_all):
        try:
            out += f(con)
        except Exception as e:                                                  # noqa: BLE001 -- never stop the watchdog
            out.append(f"{f.__module__}.{f.__name__} skipped: {e}")
    return out


def _started(unit: str) -> float | None:
    """Unix time the unit's main process started (systemd), None when unknown or not running."""
    try:
        v = subprocess.run(["systemctl", "--user", "show", "-p", "ExecMainStartTimestamp", "--timestamp=unix", "--value", unit],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        return float(v.lstrip("@")) if v.lstrip("@") else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _code_time() -> float | None:
    """Time of the last commit on the checked-out main that touched the code the services run (reins/, bin/)."""
    root = Path(__file__).resolve().parents[1]
    try:
        v = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%ct", "HEAD", "--", "reins", "bin"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        return float(v) if v else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def check_code_fresh(con, cfg, started=None, code_time=None, inflight=None, restart=None) -> list[str]:
    """A long-running service keeps the code it started with. `reins dev finish` merges into main but restarts nothing,
    so 2026-10-22 the gateway ran six hours of code that did not know the batch type `intake` and refused every intake
    batch's paid call with 401. Each pass: a service whose process started before the last code commit is restarted --
    the board at once, the gateway only when no paid call is in flight, the watchdog itself last."""
    started = started or _started
    ct = (code_time or _code_time)()
    if not ct:
        return []
    units = [("board", cfg.get("board_unit", "reins-board")), ("gateway", cfg.get("gateway_unit", "reins-gateway")),
             ("watchdog", cfg.get("watchdog_unit", "reins-watchdog"))]
    out = []
    for role, unit in units:
        t = started(unit)
        if not t or t >= ct:
            continue
        if role == "gateway":
            n = inflight() if inflight else _gateway_inflight(cfg)
            if n is None or n > 0:
                out.append(f"{unit} runs code older than main; waiting for {n if n is not None else '?'} paid call(s) to finish")
                continue
        do = restart or (lambda u: subprocess.run(["systemctl", "--user", "restart", "--no-block", u],
                                                   capture_output=True, timeout=30).returncode == 0)
        did = do(unit)
        out.append(f"{unit} restarted on new code" if did else f"{unit} runs old code; restart FAILED")
        notify.send(con, f"code_reload:{unit}", "info" if did else "action",
                    f"{unit} restarted to load the merged code" if did else f"{unit} runs old code and could not be restarted",
                    "its process started before the last commit to reins/ or bin/ on main", cooldown_min=0 if did else 60)
    return out


def _gateway_inflight(cfg) -> int | None:
    import json as _json
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg['gateway_port']}/health/deep", timeout=10) as r:
            return int(_json.loads(r.read()).get("inflight") or 0)
    except Exception:                                                            # noqa: BLE001
        return None


_GW_FAILS = 0
GW_HUNG_PASSES = 3            # this many passes (minutes) without a deep-health answer: the gateway is stuck


def check_gateway(con, cfg, probe=None, restart=None) -> list[str]:
    """The paid gateway answers its deep health check (database, single-flight lock) within 10 s, or after three passes
    it is restarted -- after its threads' stacks are dumped into the journal (SIGUSR1) so the cause can be found.
    2026-10-22: it hung for 53 minutes (every handler thread waiting on a lock, CPU 0) and no paid call went through
    for any batch; nothing noticed, because the process was alive and accepting connections."""
    global _GW_FAILS
    import json as _json
    import os as _os
    import signal as _signal
    url = f"http://127.0.0.1:{cfg['gateway_port']}/health/deep"
    try:
        if probe:
            ans = probe(url)
        else:
            with urllib.request.urlopen(url, timeout=10) as r:
                ans = _json.loads(r.read())
        ok = bool(ans.get("ok"))
    except ConnectionRefusedError:
        _GW_FAILS = 0
        return []                                       # not running at all: systemd restarts it; gateway_down covers it
    except Exception as e:                                                       # noqa: BLE001  timeout, 503, reset
        if isinstance(getattr(e, "reason", None), ConnectionRefusedError):
            _GW_FAILS = 0
            return []
        if getattr(e, "code", None) == 404 and not probe:    # a gateway started before /health/deep: its plain /health
            try:
                with urllib.request.urlopen(url.removesuffix("/deep"), timeout=10) as r:
                    _GW_FAILS = 0 if r.status == 200 else _GW_FAILS
                    return []
            except Exception as e2:                                             # noqa: BLE001
                e = e2
        ok, ans = False, {"error": str(e)[:120]}
    if ok:
        _GW_FAILS = 0
        if (ans.get("oldest_inflight_s") or 0) > 900:
            notify.send(con, "gateway_slow", "warn", f"a paid request has been in the gateway for {ans['oldest_inflight_s'] // 60} min",
                        f"threads {ans.get('threads')}, busy {ans.get('busy')}; dump: kill -USR1 $(systemctl --user show -p MainPID --value "
                        f"{cfg.get('gateway_unit', 'reins-gateway')})", cooldown_min=60)
        return []
    _GW_FAILS += 1
    if _GW_FAILS < GW_HUNG_PASSES:
        return [f"gateway did not answer its health check ({_GW_FAILS}/{GW_HUNG_PASSES}): {ans}"]
    _GW_FAILS = 0
    unit = cfg.get("gateway_unit", "reins-gateway")
    if restart:
        did = restart(unit)
    else:
        try:
            pid = int(subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit],
                                     capture_output=True, text=True, timeout=10).stdout.strip() or 0)
            if pid:
                _os.kill(pid, _signal.SIGUSR1)            # every thread's stack into the journal, then restart
                time.sleep(2)
            did = subprocess.run(["systemctl", "--user", "restart", unit], capture_output=True, timeout=60).returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            did = False
    notify.send(con, "gateway_hung", "action",
                f"paid gateway stuck for {GW_HUNG_PASSES} min: {'restarted' if did else 'RESTART FAILED'}",
                f"last answer: {ans}. Thread stacks before the restart: journalctl --user -u {unit} (search 'Thread 0x'). "
                f"In-flight paid calls failed and are retried by their clients; answers already received are cached.",
                cooldown_min=0)
    return [f"gateway hung: {'restarted' if did else 'restart failed'}"]


def _lifecycle(con) -> list[str]:
    from . import lifecycle
    try:
        return lifecycle.sweep(con)
    except Exception as e:                                                      # noqa: BLE001  never stops the watchdog
        return [f"lifecycle: {e}"]


def _services(con) -> list[str]:
    from . import knowledge
    try:
        return knowledge.watch(con)
    except Exception as e:                                                      # noqa: BLE001  never stops the watchdog
        return [f"service watch: {e}"]


def release_stale_reservations(con, cfg) -> list[str]:
    """Holds no gateway call will settle: older than `stale_reserve_min` (a call that crashed mid-flight, a gateway
    restart). Left open they count against the batch cap and the pool forever."""
    from .store import tx
    import datetime as _dt
    cutoff = (_dt.datetime.now() - _dt.timedelta(minutes=cfg.get("stale_reserve_min", 30))).isoformat(timespec="seconds")
    rows = con.execute("SELECT id, batch_id, amount FROM spend_reserve WHERE state='open' AND at<?", (cutoff,)).fetchall()
    if not rows:
        return []
    with tx(con):
        con.executemany("UPDATE spend_reserve SET state='released' WHERE id=? AND state='open'", [(r["id"],) for r in rows])
        for b in {r["batch_id"] for r in rows}:
            amt = sum(r["amount"] for r in rows if r["batch_id"] == b)
            con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?, 'reserve_released', ?)",
                        (_dt.datetime.now().isoformat(timespec="seconds"), b, f"{sum(1 for r in rows if r['batch_id'] == b)} "
                         f"stale holds (${amt:.3f}) released: no call settled them"))
    return [f"released {len(rows)} stale spend hold(s)"]


def run(interval: int = 60) -> None:
    from .store import enable_stack_dumps
    enable_stack_dumps()
    con = connect()
    while True:
        try:
            for f in once(con):
                print(f"{dt.datetime.now().isoformat(timespec='seconds')} {f}", flush=True)
        except Exception as e:                                                  # noqa: BLE001
            print(f"watchdog error: {e}", flush=True)
        time.sleep(interval)
