"""C7 launcher: the only way a batch's stage command is started.

    reins run BATCH STAGE [--module-version V] [--cwd DIR] [--retries N] -- CMD ARGS...
    reins run BATCH --self-staged [--cwd DIR] [--retries N] -- CMD ARGS...
        the command moves through several stages itself and reports each boundary with
        `reins batch stage-start / mark / stage-end` (e.g. through a project hook); on exit 0 every stage must be done
    reins ctl pause|resume|stop BATCH
    reins wait BATCH             block until the batch's process exits (reins run itself returns at once: detached)

`run` records the stage start, then hands the command to a supervisor that lives in its own session (it survives the
Claude session that launched it). The command runs in a fresh process group, so pause/stop act on the whole tree
by pgid; nobody hunts PIDs. The supervisor marks the stage done when the command exits 0 and every case is
accounted for, retries production/rework commands on failure (the command itself must be resumable), and
notifies the user either way.

Resources (C10, reins/resources.py): a stage rule in reins.toml makes the supervisor wait for its claim before starting
the command; match rules hold (SIGSTOP) a process in the tree until its claim fits. Kills (C8): a command that dies by
SIGKILL / SIGTERM (rc -9/-15/137/143) that nobody asked for, or a pid in the tree named by the kill log, pauses the batch,
records a `killed` event and notifies; nothing is retried and the next step does not start on a missing input.

Environment given to the command:
    REINS_BATCH, REINS_STAGE, REINS_MODULE_VERSION, REINS_HOME
    <ENV>_BASE_URL = http://127.0.0.1:<gateway_port>/p/<provider>[/v1]   for every provider (reins/providers.py)
    <ENV>_API_KEY  = <batch>:<stage>            (a token the gateway understands; not a real key;
                                                      self-staged: <batch>, the gateway uses the running stage)
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import batches, leases, notify, providers, resources
from .store import ReinsError, config, connect, home, now, session, tx

DEFAULT_RETRIES = {"production": 3, "rework": 3}


def _log_path(b, stage: str) -> Path:
    base = Path(b["work_dir"]) / "logs" if b["work_dir"] else home() / "logs" / b["batch_id"]
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{stage}.log"


SELF = "*"                         # stage name of a self-staged run


def start(con, batch: str, stage: str, cmd: list[str], cwd: Path, module_version: str | None = None,
          retries: int | None = None) -> dict:
    if not cmd:
        raise ReinsError("no command: reins run BATCH STAGE -- CMD ...")
    b = batches.get(con, batch)
    running = con.execute("SELECT id FROM process WHERE batch_id=? AND state IN ('running','paused')", (batch,)).fetchone()
    if running:
        raise ReinsError(f"{batch} already has a live process (#{running[0]}); reins ctl stop {batch} first")
    if stage == SELF:
        leases.check(con, f"batch:{batch}")
        if b["status"] in ("done", "failed", "closed"):
            raise ReinsError(f"batch {batch} is {b['status']}")
        if b["type"] in batches.STRICT_TYPES and not b["preflight_ok"]:
            raise ReinsError(f"{b['type']} batch: run `reins preflight {batch}` (and pass) first")
        log = _log_path(b, "run")
        warnings, mv = [], None
        if b["status"] in ("open", "paused"):
            batches.set_status(con, batch, "running", "self-staged run started", system=True) if b["status"] == "paused" else \
                con.execute("UPDATE batch SET status='running' WHERE batch_id=?", (batch,))
    else:
        log = _log_path(b, stage)
        warnings = batches.stage_start(con, batch, stage, module_version, log_path=str(log))
        mv = con.execute("SELECT module_version FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()[0]
    if retries is None:
        retries = DEFAULT_RETRIES.get(b["type"], 0)
    spec = {"batch": batch, "stage": stage, "cmd": cmd, "cwd": str(cwd.resolve()), "module_version": mv,
            "retries": retries, "log": str(log), "session": session(), "home": str(home()),
            "rules": resources.project_rules(cwd.resolve(), b["project"])}
    sup = subprocess.Popen([sys.executable, "-m", "reins.runner", "--supervise", json.dumps(spec)],
                           start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=open(str(log) + ".supervisor", "ab"), cwd=str(cwd),
                           env={**os.environ, "REINS_HOME": str(home())})
    return {"supervisor_pid": sup.pid, "log": str(log), "warnings": warnings, **_started(con, sup)}


def _started(con, sup, secs: float = 10) -> dict:
    """What the detached supervisor did first: the process row it registered, or the claim it waits on."""
    t0 = time.time()
    while time.time() - t0 < secs:
        p = con.execute("SELECT id, pgid FROM process WHERE supervisor_pid=? ORDER BY id DESC LIMIT 1", (sup.pid,)).fetchone()
        if p:
            return {"process": p["id"], "pgid": p["pgid"]}
        c = con.execute("SELECT id, rule FROM resource_claim WHERE owner_pid=? AND state='waiting'", (sup.pid,)).fetchone()
        if c:
            return {"waiting_claim": c["id"], "rule": c["rule"]}
        if sup.poll() is not None:
            return {"exited": sup.returncode}
        time.sleep(0.1)
    return {}


def wait(con, batch: str, timeout_s: float | None = None, poll_s: float = 2) -> dict:
    """Block until nothing of the batch runs: no live process, no supervisor waiting for resources."""
    batches.get(con, batch)
    t0 = time.time()
    while True:
        live = [p for p in live_processes(con, batch) if _alive(p["pgid"]) or _alive(p["supervisor_pid"])]
        waiting = [c for c in resources.live(con) if c["batch_id"] == batch and c["state"] == "waiting" and c["pid"] is None]
        if not live and not waiting:
            b = batches.get(con, batch)
            return {"status": b["status"], "reason": b["status_reason"], "timed_out": False}
        if timeout_s is not None and time.time() - t0 > timeout_s:
            b = batches.get(con, batch)
            return {"status": b["status"], "reason": b["status_reason"], "timed_out": True}
        time.sleep(poll_s)


def _env(spec: dict) -> dict:
    port = config()["gateway_port"]
    token = spec["batch"] if spec["stage"] == SELF else f"{spec['batch']}:{spec['stage']}"
    return {**os.environ, "REINS_BATCH": spec["batch"], "REINS_STAGE": spec["stage"], "REINS_HOME": spec["home"],
            "REINS_MODULE_VERSION": spec["module_version"] or "", "PYTHONUNBUFFERED": "1",
            "REINS_BIN": str(Path(__file__).resolve().parents[1] / "bin" / "reins"),
            **providers.client_env(port, token)}


def _on_kill(con, batch: str, stage: str, pid: int, cmd: str, how: str) -> None:
    """A process of the batch was killed by someone other than reins: record it, pause the batch so its command does not
    go on to the next step on a missing input, tell the user. Once per pid."""
    if con.execute("SELECT 1 FROM batch_event WHERE batch_id=? AND event='killed' AND detail LIKE ?",
                   (batch, f"pid {pid} %")).fetchone():
        return
    detail = f"pid {pid} ({cmd[:200]}) {how}"
    with tx(con):
        batches._event(con, batch, "killed", detail[:1000])
    why = f"{resources.KILLED} pid {pid} {how}"[:300]
    try:
        if batches.get(con, batch)["status"] == "running":
            ctl(con, "pause", batch, why, system=True)
    except ReinsError:
        pass
    notify.send(con, f"killed:{batch}:{pid}", "action", f"{batch} paused: a process was killed",
                f"{detail[:600]}\nthe step it ran did not finish. Either: reins ctl stop {batch}, then restart from that step;"
                f" or: reins ctl resume {batch} --after-kill (the command goes on as it is)", cooldown_min=0)


def supervise(spec: dict) -> int:
    os.environ["REINS_HOME"] = spec["home"]
    con = connect()
    try:
        return _supervise(con, spec)
    finally:
        resources.release_owner(con, os.getpid())


def _supervise(con, spec: dict) -> int:
    batch, stage = spec["batch"], spec["stage"]
    rs = spec.get("rules") or []
    rule = resources.stage_rule(rs, stage) if stage != SELF else None
    if rule:
        try:
            resources.acquire(con, batch, stage, rule, owner_pid=os.getpid())
        except ReinsError as e:                      # stopped while it waited
            notify.send(con, f"stopped:{batch}", "info", f"{batch}/{stage} did not start", str(e), cooldown_min=0)
            return 1
    watch = any(r.get("match") for r in rs) or bool(config()["kill_watch"]["cmd"])
    attempt = 0
    while True:
        attempt += 1
        with open(spec["log"], "ab") as logf:
            logf.write(f"\n=== reins attempt {attempt} {now()} :: {shlex.join(spec['cmd'])}\n".encode())
            logf.flush()
            child = subprocess.Popen(spec["cmd"], cwd=spec["cwd"], env=_env(spec), start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)
            started_at = now()
            with tx(con):
                cur = con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, session,"
                                  " state, attempt, started) VALUES (?,?,?,?,?,?,?,?, 'running', ?, ?)",
                                  (batch, stage, child.pid, os.getpid(), shlex.join(spec["cmd"]), spec["cwd"],
                                   spec["log"], spec["session"], attempt, now()))
                pid_row = cur.lastrowid
            mon = None
            if watch:
                mon = resources.Monitor(batch, stage, child.pid, rs,
                                        lambda pid, cmd, line: _on_kill(connect(), batch, stage, pid, cmd,
                                                                        f"named by the kill log: {line[:300]}"))
                mon.start()
            code = child.wait()
            if mon:
                mon.stop()
        state = con.execute("SELECT state FROM process WHERE id=?", (pid_row,)).fetchone()[0]
        with tx(con):
            con.execute("UPDATE process SET state=CASE WHEN state='stopped' THEN 'stopped' ELSE 'exited' END,"
                        " exit_code=?, ended=? WHERE id=?", (code, now(), pid_row))
        if state == "stopped":
            notify.send(con, f"stopped:{batch}", "info", f"{batch}/{stage} stopped by request", "", cooldown_min=0)
            return 0
        if code in resources.KILL_CODES:
            _on_kill(con, batch, stage, child.pid, shlex.join(spec["cmd"]), f"exited {code} (killed by a signal)")
            return code
        killed = con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='killed' AND at>=?"
                             " ORDER BY id DESC LIMIT 1", (batch, started_at)).fetchone()
        if code != 0 and killed:                     # a child was killed and the command failed for it: no retry
            return code
        if code == 0 and stage == SELF:
            open_ = [r[0] for r in con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status NOT IN ('done','skipped')"
                                               " ORDER BY ord", (batch,))]
            if open_:
                notify.send(con, f"stage_incomplete:{batch}", "action", f"{batch}: command exited 0 but stages are not finished",
                            f"not done: {', '.join(open_)}. log: {spec['log']}", cooldown_min=0)
            else:
                notify.send(con, f"done:{batch}", "info", f"{batch}: all stages done", f"log: {spec['log']}", cooldown_min=0)
            return 0
        if code == 0:
            try:
                led = batches.stage_end(con, batch, stage)
                if led["status"] == "done":
                    notify.send(con, f"stage_done:{batch}:{stage}", "info", f"{batch}: stage {stage} done",
                                f"done={led['done']} skipped={led['skipped']} failed={led['failed']}", cooldown_min=0)
            except ReinsError as e:
                notify.send(con, f"stage_incomplete:{batch}:{stage}", "action",
                            f"{batch}: {stage} exited 0 but the case ledger is incomplete", str(e), cooldown_min=0)
            return 0
        if attempt <= spec["retries"]:
            notify.send(con, f"retry:{batch}:{stage}", "warn", f"{batch}/{stage} exited {code}; retry {attempt}/{spec['retries']}",
                        f"log: {spec['log']}", cooldown_min=0)
            time.sleep(min(30 * attempt, 300))
            continue
        try:
            batches.set_status(con, batch, "paused", f"{stage} exited {code} after {attempt} attempt(s)", system=True)
        except ReinsError:
            pass
        notify.send(con, f"failed:{batch}:{stage}", "action", f"{batch} paused: {stage} exited {code}",
                    f"log: {spec['log']}\nfix, then: reins ctl resume {batch}  or  reins run {batch} {stage} -- ...",
                    cooldown_min=0)
        return code


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}").exists() and "zombie" not in (Path(f"/proc/{pid}/status").read_text().split("State:")[1][:12])
    except (ProcessLookupError, PermissionError, OSError, IndexError):
        return False


def live_processes(con, batch: str) -> list:
    return con.execute("SELECT * FROM process WHERE batch_id=? AND state IN ('running','paused') ORDER BY id", (batch,)).fetchall()


def ctl(con, verb: str, batch: str, why: str = "", system: bool = False, after_kill: bool = False) -> str:
    """pause -> SIGSTOP the process group; resume -> SIGCONT; stop -> SIGTERM, then SIGKILL after 15 s.
    A batch paused because a process was killed resumes only with after_kill (its step did not finish)."""
    if not system:
        leases.check(con, f"batch:{batch}")
    procs = live_processes(con, batch)
    b = batches.get(con, batch)
    if verb == "resume" and (b["status_reason"] or "").startswith(resources.KILLED) and not after_kill:
        raise ReinsError(f"{batch} was paused because a process was killed ({b['status_reason']}); the step it ran did not "
                         f"finish. Either: reins ctl stop {batch}, then restart from that step; or, if the command "
                         f"redoes it itself: reins ctl resume {batch} --after-kill")
    if verb == "pause":
        for p in procs:
            try:
                os.killpg(p["pgid"], signal.SIGSTOP)
            except ProcessLookupError:
                pass
            con.execute("UPDATE process SET state='paused' WHERE id=?", (p["id"],))
        if b["status"] == "running":
            batches.set_status(con, batch, "paused", why or "paused by request", system=True)
        return f"paused {len(procs)} process group(s)"
    if verb == "resume":
        for p in procs:
            try:
                os.killpg(p["pgid"], signal.SIGCONT)
            except ProcessLookupError:
                pass
            con.execute("UPDATE process SET state='running' WHERE id=?", (p["id"],))
        held = [c["pid"] for c in resources.live(con) if c["batch_id"] == batch and c["state"] == "waiting" and c["pid"]]
        for pid in held:                             # still waiting for resources: they stay stopped
            resources._signal(resources._subtree(resources.tree(pid), pid), signal.SIGSTOP)
        if after_kill:
            with tx(con):
                batches._event(con, batch, "resume_after_kill", why or "resumed after a kill")
        if b["status"] == "paused":
            batches.set_status(con, batch, "running", why or "resumed", system=True)
        return f"resumed {len(procs)} process group(s)" if procs else \
            "no live process: batch marked running; start the stage again with reins run"
    if verb == "stop":
        with tx(con):                                # a supervisor still waiting for resources gives up
            con.execute("UPDATE resource_claim SET state='released', released=?, note='stopped while waiting'"
                        " WHERE batch_id=? AND state='waiting'", (now(), batch))
        for p in procs:
            con.execute("UPDATE process SET state='stopped' WHERE id=?", (p["id"],))     # the supervisor must not retry
            for sig, wait in ((signal.SIGCONT, 0), (signal.SIGTERM, 15), (signal.SIGKILL, 0)):
                try:
                    os.killpg(p["pgid"], sig)
                except ProcessLookupError:
                    break
                t0 = time.time()
                while wait and _alive(p["pgid"]) and time.time() - t0 < wait:
                    time.sleep(0.5)
                if not _alive(p["pgid"]):
                    break
        if b["status"] == "running":
            batches.set_status(con, batch, "paused", why or "stopped by request", system=True)
        return f"stopped {len(procs)} process group(s)"
    raise ReinsError(f"unknown verb {verb}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--supervise":
        sys.exit(supervise(json.loads(sys.argv[2])))
    sys.exit("internal entry point; use: reins run ...")
