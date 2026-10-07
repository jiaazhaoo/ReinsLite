"""C7 launcher: the only way a batch's stage command is started.

    reins run BATCH STAGE [--module-version V] [--cwd DIR] [--retries N] -- CMD ARGS...
    reins run BATCH --self-staged [--cwd DIR] [--retries N] -- CMD ARGS...
        the command moves through several stages itself and reports each boundary with
        `reins batch stage-start / mark / stage-end` (e.g. through a project hook); on exit 0 every stage must be done
    reins ctl pause|resume|stop BATCH

`run` records the stage start, then hands the command to a supervisor that lives in its own session (it survives the
Claude session that launched it). The command runs in a fresh process group, so pause/stop act on the whole tree
by pgid; nobody hunts PIDs. The supervisor marks the stage done when the command exits 0 and every case is
accounted for, retries production/rework commands on failure (the command itself must be resumable), and
notifies the user either way.

Environment given to the command:
    REINS_BATCH, REINS_STAGE, REINS_MODULE_VERSION, REINS_HOME
    OPENROUTER_BASE_URL = http://127.0.0.1:<gateway_port>/api/v1
    OPENROUTER_API_KEY  = <batch>:<stage>            (a token the gateway understands; not a real key;
                                                      self-staged: <batch>, the gateway uses the running stage)
    DEEPSEEK_BASE_URL   = http://127.0.0.1:<gateway_port>/p/deepseek/v1, DEEPSEEK_API_KEY = the same token
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

from . import batches, leases, notify
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
            "retries": retries, "log": str(log), "session": session(), "home": str(home())}
    sup = subprocess.Popen([sys.executable, "-m", "reins.runner", "--supervise", json.dumps(spec)],
                           start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=open(str(log) + ".supervisor", "ab"), cwd=str(cwd),
                           env={**os.environ, "REINS_HOME": str(home())})
    return {"supervisor_pid": sup.pid, "log": str(log), "warnings": warnings}


def _env(spec: dict) -> dict:
    port = config()["gateway_port"]
    token = spec["batch"] if spec["stage"] == SELF else f"{spec['batch']}:{spec['stage']}"
    return {**os.environ, "REINS_BATCH": spec["batch"], "REINS_STAGE": spec["stage"], "REINS_HOME": spec["home"],
            "REINS_MODULE_VERSION": spec["module_version"] or "", "PYTHONUNBUFFERED": "1",
            "REINS_BIN": str(Path(__file__).resolve().parents[1] / "bin" / "reins"),
            "OPENROUTER_BASE_URL": f"http://127.0.0.1:{port}/api/v1", "OPENROUTER_API_KEY": token,
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{port}/p/deepseek/v1", "DEEPSEEK_API_KEY": token}


def supervise(spec: dict) -> int:
    os.environ["REINS_HOME"] = spec["home"]
    con = connect()
    batch, stage = spec["batch"], spec["stage"]
    attempt = 0
    while True:
        attempt += 1
        with open(spec["log"], "ab") as logf:
            logf.write(f"\n=== reins attempt {attempt} {now()} :: {shlex.join(spec['cmd'])}\n".encode())
            logf.flush()
            child = subprocess.Popen(spec["cmd"], cwd=spec["cwd"], env=_env(spec), start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)
            with tx(con):
                cur = con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, session,"
                                  " state, attempt, started) VALUES (?,?,?,?,?,?,?,?, 'running', ?, ?)",
                                  (batch, stage, child.pid, os.getpid(), shlex.join(spec["cmd"]), spec["cwd"],
                                   spec["log"], spec["session"], attempt, now()))
                pid_row = cur.lastrowid
            code = child.wait()
        state = con.execute("SELECT state FROM process WHERE id=?", (pid_row,)).fetchone()[0]
        with tx(con):
            con.execute("UPDATE process SET state=CASE WHEN state='stopped' THEN 'stopped' ELSE 'exited' END,"
                        " exit_code=?, ended=? WHERE id=?", (code, now(), pid_row))
        if state == "stopped":
            notify.send(con, f"stopped:{batch}", "info", f"{batch}/{stage} stopped by request", "", cooldown_min=0)
            return 0
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


def ctl(con, verb: str, batch: str, why: str = "", system: bool = False) -> str:
    """pause -> SIGSTOP the process group; resume -> SIGCONT; stop -> SIGTERM, then SIGKILL after 15 s."""
    if not system:
        leases.check(con, f"batch:{batch}")
    procs = live_processes(con, batch)
    b = batches.get(con, batch)
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
        if b["status"] == "paused":
            batches.set_status(con, batch, "running", why or "resumed", system=True)
        return f"resumed {len(procs)} process group(s)" if procs else \
            "no live process: batch marked running; start the stage again with reins run"
    if verb == "stop":
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
