"""C10 resource admission: what a stage or a step needs (RAM, GPU memory, exclusive use of something) is declared in the
project's reins.toml; one ledger of live claims across every batch sits in reins.db; a claim that would go past the
machine budget waits its turn instead of starting.

reins.toml (project words and numbers live here, never in the framework):

    [resources.ocr]                       # a name the project picks
    match = "s3_georef\\.ocr_rotated"     # regex on the command line of any process in a batch's process tree ...
    gpu_gb = 8
    exclusive = ["gpu"]                   # ... at most one granted claim holds a tag at a time
    [resources.judge]
    match = "qa_judge\\.py"
    mem_gb = 11
    [resources.check]
    stage = "check"                       # ... or a whole stage (reins run B STAGE, or reins batch stage-start)
    mem_gb = 4

config.toml (the machine):

    [resources]
    mem_total_gb = 0        # 0 = read /proc/meminfo
    mem_reserved_gb = 0     # what the rest of the machine uses (desktop, services, sessions)
    gpu_total_gb = 0        # 0 = ask nvidia-smi (first GPU); no GPU found = only tags apply to GPU claims
    gpu_reserved_gb = 0
    max_fraction = 0.75     # granted claims stay under total * max_fraction - reserved
    poll_s = 1.0            # how often a supervisor looks at its process tree

Measured, not guessed (2026-11-04): the supervisor samples every claimed process tree (RSS, GPU memory per pid) every
5 s and records its peak when it ends (resource_peak). A match claim asks for max(declared, 1.1 x the largest peak of
the rule's last 10 runs), so a declaration that is too small corrects itself after one run.
Adaptive (2026-11-05, user: 我不允许这样的事情发生，必须有很灵活聪明的调整机制): peaks are learned per subcommand -- the
first plain word after the matched part of the command line (`yolo_plans.py yolo` and `yolo_plans.py fallback` are two
signatures of one rule; a Qwen fallback peak no longer sizes every YOLO pass) -- falling back to the whole rule until the
signature has a peak of its own. And a granted claim follows its process: once the process has run SETTLE_S, its claim is
set to max(the rule's declared need, live peak x RESIZE_MARGIN) -- lowered when the process uses far less than it was
given (waiting claims fit as soon as there is real room), raised when it uses more than its claim.
    split_env = "E2E_GPU_PROCS"          # the rule's gpu_gb is for the whole step; each process holds 1/N of it,
                                          # N read from that variable in the process's environment (shards)
A release cut before its project declared any [resources] uses the main checkout's (the needs are the machine's).

Production and rework first: their claims do not wait behind waiting experiment claims, and an experiment claim
waits while a production claim is waiting. Otherwise order is first come, first served: a claim is granted only when it fits and no older claim of a running batch is still
waiting (no overtaking, so a big claim is never starved by a stream of small ones). A claim bigger than the whole
budget is granted only when nothing else holds that dimension (it runs alone).

Where a claim waits:
    stage rule, `reins run B STAGE`   the supervisor waits before starting the command
    stage rule, self-staged           `reins batch stage-start` blocks (the command is calling it, so it waits too)
    match rule                        the supervisor stops (SIGSTOP) the matching process and its children within
                                      poll_s of it appearing, and continues it (SIGCONT) when its claim is granted
A claim ends when its process (or the process that owns it) is gone, or at stage end.

Kill detection (also in the supervisor): config.toml

    [kill_watch]
    cmd = "journalctl --user -f -n 0 -o cat"       # a command that follows a log; empty = off
    pattern = "killing pid=(?P<pid>\\d+)"          # a line naming a pid that was in a batch's tree = a kill
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from .store import ReinsError, config, connect, now, session, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_claim (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id   TEXT NOT NULL,
  stage      TEXT,
  rule       TEXT NOT NULL,                          -- the [resources.<name>] that asked
  pid        INTEGER,                                -- the process the claim is for (match rules)
  owner_pid  INTEGER,                                -- the claim ends when this process is gone
  cmd        TEXT,
  mem_gb     REAL NOT NULL DEFAULT 0,
  gpu_gb     REAL NOT NULL DEFAULT 0,
  tags       TEXT NOT NULL DEFAULT '[]',             -- JSON list of exclusive tags
  state      TEXT NOT NULL CHECK (state IN ('waiting', 'granted', 'released')),
  requested  TEXT NOT NULL,
  granted    TEXT,
  released   TEXT,
  note       TEXT,
  session    TEXT,
  sig        TEXT,                                   -- subcommand signature (see sig_of)
  need_mem   REAL,                                   -- the rule's declared need for this process (resize floor)
  need_gpu   REAL,
  peak_mem   REAL,                                   -- live peak of the claimed process tree so far (rebalance)
  peak_gpu   REAL
);
CREATE INDEX IF NOT EXISTS resource_claim_live ON resource_claim (state, id);
CREATE TABLE IF NOT EXISTS resource_peak (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  batch_id   TEXT NOT NULL,
  rule       TEXT NOT NULL,
  pid        INTEGER,
  mem_gb     REAL NOT NULL,                          -- peak RSS of the claimed process and its children
  gpu_gb     REAL NOT NULL,                          -- peak GPU memory of the same tree
  share      INTEGER NOT NULL DEFAULT 1,             -- split_env value: the process held 1/share of the rule's GPU
  seconds    REAL,
  sig        TEXT                                    -- subcommand signature of the process (see sig_of)
);
CREATE INDEX IF NOT EXISTS resource_peak_rule ON resource_peak (rule, id);
"""

PRIORITY_TYPES = ("production", "rework")
KILL_CODES = {-signal.SIGKILL, -signal.SIGTERM, 128 + signal.SIGKILL, 128 + signal.SIGTERM}
KILLED = "killed:"                       # batch.status_reason prefix of a pause caused by a kill


def ensure(con) -> None:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_peak'").fetchone():
        con.executescript(SCHEMA)
    for t, cols in (("resource_peak", ("sig TEXT",)),                   # 2026-11-05: adaptive claims
                    ("resource_claim", ("sig TEXT", "need_mem REAL", "need_gpu REAL", "peak_mem REAL", "peak_gpu REAL"))):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        for c in cols:
            if c.split()[0] not in have:
                con.execute(f"ALTER TABLE {t} ADD COLUMN {c}")


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        st = Path(f"/proc/{pid}/status").read_text()
        return "zombie" not in st.split("State:")[1][:12]
    except (ProcessLookupError, PermissionError, OSError, IndexError):
        return False


# ---------------------------------------------------------------- rules and budget

def rules(project_cfg: dict) -> list[dict]:
    out = []
    for name, r in (project_cfg.get("resources") or {}).items():
        out.append({"name": name, "stage": r.get("stage"), "match": r.get("match"),
                    "mem_gb": float(r.get("mem_gb", 0)), "gpu_gb": float(r.get("gpu_gb", 0)),
                    "tags": sorted(r.get("exclusive", [])), "split_env": r.get("split_env")})
    return out


def check_rules(project_cfg: dict) -> list[str]:
    out = []
    for name, r in (project_cfg.get("resources") or {}).items():
        if bool(r.get("stage")) == bool(r.get("match")):
            out.append(f"[resources.{name}] needs exactly one of stage = \"...\" or match = \"regex\"")
        if r.get("match"):
            try:
                re.compile(r["match"])
            except re.error as e:
                out.append(f"[resources.{name}] match does not compile: {e}")
        if not (r.get("mem_gb") or r.get("gpu_gb") or r.get("exclusive")):
            out.append(f"[resources.{name}] declares nothing (mem_gb, gpu_gb or exclusive)")
        bad = set(r) - {"stage", "match", "mem_gb", "gpu_gb", "exclusive", "about", "split_env"}
        if bad:
            out.append(f"[resources.{name}] unknown keys: {', '.join(sorted(bad))}")
    return out


def project_rules(cwd: Path | None, project: str | None = None) -> list[dict]:
    """The rules of the checkout a command runs from (its reins.toml is the code version being run); else the project's
    main checkout."""
    import tomllib
    for d in ([cwd, *cwd.parents] if cwd else []):
        f = d / "reins.toml"
        if f.is_file():
            try:
                rs = rules(tomllib.loads(f.read_text(encoding="utf-8")))
            except (OSError, tomllib.TOMLDecodeError):
                rs = []
            if rs or not project:
                return rs
            break                    # a release cut before its project declared resources: the machine's needs are
                                     # the main checkout's (2026-11-04), not "none"
    if project:
        from . import projects
        for _, cfg in projects.all_():
            if cfg.get("project") == project:
                return rules(cfg)
    return []


def stage_rule(rs: list[dict], stage: str) -> dict | None:
    return next((r for r in rs if r["stage"] == stage), None)


_DETECTED: dict[str, float] = {}


def _detect_mem() -> float:
    if "mem" not in _DETECTED:
        kb = next(int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal:"))
        _DETECTED["mem"] = kb / 1024 / 1024
    return _DETECTED["mem"]


def _detect_gpu() -> float:
    if "gpu" not in _DETECTED:
        try:
            q = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10)
            _DETECTED["gpu"] = int(q.stdout.split()[0]) / 1024 if q.returncode == 0 and q.stdout.strip() else 0.0
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            _DETECTED["gpu"] = 0.0
    return _DETECTED["gpu"]


def budget(cfg: dict | None = None) -> dict:
    """What all granted claims together may hold: {'mem': GB, 'gpu': GB or None (no GPU known)}."""
    c = (cfg or config())["resources"]
    mem_total = c["mem_total_gb"] or _detect_mem()
    gpu_total = c["gpu_total_gb"] or _detect_gpu()
    return {"mem": mem_total * c["max_fraction"] - c["mem_reserved_gb"],
            "gpu": gpu_total * c["max_fraction"] - c["gpu_reserved_gb"] if gpu_total else None,
            "mem_total": mem_total, "gpu_total": gpu_total or None, "max_fraction": c["max_fraction"]}


def fits(granted: list, need: dict, cap: dict) -> tuple[bool, str]:
    """Pure: can `need` be granted next to the `granted` claims within `cap`? Returns (yes, why not)."""
    tags = set(need.get("tags") or [])
    for g in granted:
        shared = tags & set(json.loads(g["tags"]) if isinstance(g["tags"], str) else g["tags"])
        if shared:
            return False, f"{', '.join(sorted(shared))} held by claim #{g['id']} ({g['batch_id']} {g['rule']})"
    for dim in ("mem", "gpu"):
        want = need.get(f"{dim}_gb") or 0
        if not want or cap.get(dim) is None:
            continue
        held = sum(g[f"{dim}_gb"] or 0 for g in granted)
        if held and held + want > cap[dim]:             # nothing held: a claim bigger than the budget runs alone
            return False, f"{dim} {held:g}+{want:g} GB > budget {cap[dim]:.1f} GB"
    return True, ""


# ---------------------------------------------------------------- the ledger

def _batch_event(con, batch: str, event: str, detail: str) -> None:
    con.execute("INSERT INTO batch_event (at, batch_id, event, detail, session) VALUES (?,?,?,?,?)",
                (now(), batch, event, detail, session()))


def cleanup(con) -> int:
    """Release claims whose process or owner is gone (a crash, a kill, a stop): the ledger never keeps a ghost."""
    ensure(con)
    gone = [r["id"] for r in con.execute("SELECT id, pid, owner_pid FROM resource_claim WHERE state!='released'")
            if (r["pid"] and not alive(r["pid"])) or (r["owner_pid"] and not alive(r["owner_pid"]))]
    if gone:
        with tx(con):
            con.executemany("UPDATE resource_claim SET state='released', released=?, note=COALESCE(note,'') || ' process gone'"
                            " WHERE id=? AND state!='released'", [(now(), i) for i in gone])
    return len(gone)


LEARN_N = 10              # peaks of the last N finished claims of a rule
LEARN_MARGIN = 1.1
SETTLE_S = 120.0          # a running process's claim follows its live peak after this long
RESIZE_MARGIN = 1.25      # live peak x this = the settled claim (never below the rule's declared need)
_WORD = re.compile(r"[A-Za-z][\w.-]*$")


def sig_of(rule: dict, cmd: str | None) -> str:
    """The subcommand signature: the first plain word (not an option, path or number) after the rule's match in the
    command line -- 'fallback' in '... yolo_plans.py fallback --out-root X'; '' when there is none."""
    m = re.search(rule.get("match") or "$^", cmd or "")
    if not m:
        return ""
    for tok in (cmd or "")[m.end():].split():
        if tok.startswith("-") or "/" in tok or "=" in tok:
            return ""                                                   # options first: no subcommand
        return tok if _WORD.match(tok) else ""
    return ""


def learned(con, rule: str, sig: str | None = None) -> dict:
    """What the rule's processes really used lately: {'mem': GB, 'gpu': GB for the whole rule (share x per process)}.
    With `sig`, the peaks of that subcommand when it has any (else the rule's)."""
    ensure(con)
    rows = []
    if sig:
        rows = con.execute("SELECT mem_gb, gpu_gb, share FROM resource_peak WHERE rule=? AND sig=? ORDER BY id DESC LIMIT ?",
                           (rule, sig, LEARN_N)).fetchall()
    if not rows:
        rows = con.execute("SELECT mem_gb, gpu_gb, share FROM resource_peak WHERE rule=? ORDER BY id DESC LIMIT ?",
                           (rule, LEARN_N)).fetchall()
    return {"mem": max((r["mem_gb"] for r in rows), default=0.0), "gpu": max((r["gpu_gb"] * r["share"] for r in rows), default=0.0),
            "n": len(rows)}


def _env_of(pid: int | None, name: str | None) -> str | None:
    if not pid or not name:
        return None
    try:
        for kv in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
            if kv.startswith(name.encode() + b"="):
                return kv.split(b"=", 1)[1].decode(errors="replace")
    except OSError:
        pass
    return None


def share_of(rule: dict, pid: int | None) -> int:
    try:
        return max(1, int(_env_of(pid, rule.get("split_env")) or 1))
    except ValueError:
        return 1


def sized(con, rule: dict, pid: int | None, sig: str | None = None) -> tuple[float, float, int]:
    """(mem_gb, gpu_gb, share) a claim asks for: the declared need or what was measured (x1.1), whichever is larger;
    a split_env rule's GPU is shared by that many processes; measured = the subcommand's peaks when it has any."""
    lr = learned(con, rule["name"], sig)
    n = share_of(rule, pid)
    mem = max(rule["mem_gb"], round(lr["mem"] * LEARN_MARGIN, 2))
    gpu = max(rule["gpu_gb"], round(lr["gpu"] * LEARN_MARGIN, 2)) / n
    return mem, round(gpu, 2), n


def request(con, batch: str, stage: str | None, rule: dict, pid: int | None = None, owner_pid: int | None = None,
            cmd: str | None = None) -> int:
    ensure(con)
    sig = sig_of(rule, cmd) if rule.get("match") else ""
    n = share_of(rule, pid) if rule.get("match") else 1
    need = (rule.get("mem_gb") or 0.0, (rule.get("gpu_gb") or 0.0) / n)   # declared, for this process
    if rule.get("match"):
        mem, gpu, _ = sized(con, rule, pid, sig)
        rule = {**rule, "mem_gb": mem, "gpu_gb": gpu}
    with tx(con):
        cur = con.execute("INSERT INTO resource_claim (batch_id, stage, rule, pid, owner_pid, cmd, mem_gb, gpu_gb, tags,"
                          " state, requested, session, sig, need_mem, need_gpu) VALUES (?,?,?,?,?,?,?,?,?, 'waiting', ?, ?, ?, ?, ?)",
                          (batch, stage, rule["name"], pid, owner_pid, (cmd or "")[:500], rule["mem_gb"], rule["gpu_gb"],
                           json.dumps(rule["tags"]), now(), session(), sig or None, *need))
        return cur.lastrowid


def try_grant(con, claim_id: int, cap: dict | None = None) -> tuple[bool, str]:
    """Grant the claim when it fits and no older claim of a running batch is still waiting. (granted, why not)."""
    cleanup(con)
    cap = cap or budget()
    with tx(con):
        c = con.execute("SELECT * FROM resource_claim WHERE id=?", (claim_id,)).fetchone()
        if c is None or c["state"] == "released":
            raise ReinsError(f"resource claim #{claim_id} is released")
        if c["state"] == "granted":
            return True, ""
        mine = con.execute("SELECT type FROM batch WHERE batch_id=?", (c["batch_id"],)).fetchone()
        first = mine is not None and mine["type"] in PRIORITY_TYPES      # production / rework go before experiments
        older = con.execute("SELECT c.id, c.batch_id, c.rule FROM resource_claim c JOIN batch b USING (batch_id)"
                            " WHERE c.state='waiting' AND c.id<? AND b.status='running'"
                            + (" AND b.type IN (%s)" % ",".join("?" * len(PRIORITY_TYPES)) if first else "")
                            + " ORDER BY c.id LIMIT 1", (claim_id, *(PRIORITY_TYPES if first else ()))).fetchone()
        if not older and not first:                                   # an experiment also lets a waiting production
            older = con.execute("SELECT c.id, c.batch_id, c.rule FROM resource_claim c JOIN batch b USING (batch_id)"  # claim go first
                                " WHERE c.state='waiting' AND c.id>? AND b.status='running' AND b.type IN (%s) ORDER BY c.id LIMIT 1"
                                % ",".join("?" * len(PRIORITY_TYPES)), (claim_id, *PRIORITY_TYPES)).fetchone()
        if older:
            return False, f"behind claim #{older['id']} ({older['batch_id']} {older['rule']})"
        granted = con.execute("SELECT * FROM resource_claim WHERE state='granted'").fetchall()
        ok, why = fits(granted, dict(c), cap)
        if not ok:
            return False, why
        con.execute("UPDATE resource_claim SET state='granted', granted=? WHERE id=?", (now(), claim_id))
        _batch_event(con, c["batch_id"], "resource_granted",
                     f"#{claim_id} {c['rule']} mem {c['mem_gb']:g} GB gpu {c['gpu_gb']:g} GB {c['tags']}"
                     + (f" pid {c['pid']}" if c["pid"] else ""))
        return True, ""


def resize(con, claim_id: int, peak_mem: float, peak_gpu: float, need_mem: float, need_gpu: float, age_s: float) -> dict:
    """A granted claim follows its process (adaptive claims): `peak_*` = what the process tree used so far, `need_*` =
    the rule's declared need for this process. Raised at once when the process uses more than its claim (x LEARN_MARGIN);
    after SETTLE_S lowered to max(need, peak x RESIZE_MARGIN) when that is under 90% of the claim. -> the changed dims."""
    c = con.execute("SELECT * FROM resource_claim WHERE id=?", (claim_id,)).fetchone()
    if c is None or c["state"] != "granted":
        return {}
    new = {}
    for dim, peak, need in (("mem", peak_mem, need_mem), ("gpu", peak_gpu, need_gpu)):
        cur = c[f"{dim}_gb"] or 0.0
        if not cur and not need:
            continue                                                    # the rule does not claim this dimension
        if peak > cur:
            new[dim] = round(peak * LEARN_MARGIN, 2)
        elif age_s >= SETTLE_S:
            target = round(max(need, peak * RESIZE_MARGIN), 2)
            if target < cur * 0.9:
                new[dim] = target
    if new:
        with tx(con):
            con.execute("UPDATE resource_claim SET " + ", ".join(f"{d}_gb=?" for d in new) + " WHERE id=? AND state='granted'",
                        (*new.values(), claim_id))
            _batch_event(con, c["batch_id"], "resource_resized",
                         f"#{claim_id} {c['rule']}" + (f" {c['sig']}" if c["sig"] else "") + ": "
                         + ", ".join(f"{d} {c[f'{d}_gb']:g} -> {v:g} GB" for d, v in new.items())
                         + f" (live peak mem {peak_mem:.2f} gpu {peak_gpu:.2f} GB after {age_s:.0f} s)")
    return new


IDLE_WAIT_S = 600.0       # a claim waiting this long while the GPU's real use is under half the budget is recorded


def sig_peak(con, rule: str, sig: str | None) -> tuple[float, float]:
    """(mem, gpu) peak of this subcommand's own last runs (per process), 0 when it has none: a resize floor, so a
    process known to peak late keeps that room even while it still uses little."""
    if not sig:
        return 0.0, 0.0
    ensure(con)
    rows = con.execute("SELECT mem_gb, gpu_gb FROM resource_peak WHERE rule=? AND sig=? ORDER BY id DESC LIMIT ?",
                       (rule, sig, LEARN_N)).fetchall()
    return (max((r["mem_gb"] for r in rows), default=0.0), max((r["gpu_gb"] for r in rows), default=0.0))


def _need_of(con, c) -> tuple[float, float] | None:
    """A claim's declared need: stored on it (since 2026-11-05) or the rule of its batch's project; None if unknown."""
    if c["need_mem"] is not None or c["need_gpu"] is not None:
        return (c["need_mem"] or 0.0, c["need_gpu"] or 0.0)
    b = con.execute("SELECT project FROM batch WHERE batch_id=?", (c["batch_id"],)).fetchone()
    r = next((r for r in project_rules(None, b["project"] if b else None) if r["name"] == c["rule"]), None)
    if r is None:
        return None
    n = share_of(r, c["pid"]) if r.get("match") else 1
    return (r.get("mem_gb") or 0.0, (r.get("gpu_gb") or 0.0) / n)


def rebalance(con, cap: dict | None = None) -> list[str]:
    """Every pass of the watchdog, for every granted claim of a live process, whichever supervisor (old or new code)
    holds it: measure the process tree's RAM / GPU now, keep the peak on the claim, resize it (resize()). Then a claim
    that has waited IDLE_WAIT_S while the GPU's real use is under half the budget is recorded on its batch (once)."""
    ensure(con)
    out = []
    rows = con.execute("SELECT * FROM resource_claim WHERE state='granted' AND pid IS NOT NULL").fetchall()
    if rows:
        table, gm = _proc_table(), gpu_by_pid()
        for c in rows:
            if c["pid"] not in table:
                continue
            sub = list(tree(c["pid"], table))
            mem, gpu = sum(rss_gb(q) for q in sub), sum(gm.get(q, 0.0) for q in sub)
            pm, pg = max(c["peak_mem"] or 0.0, mem), max(c["peak_gpu"] or 0.0, gpu)
            with tx(con):
                con.execute("UPDATE resource_claim SET peak_mem=?, peak_gpu=? WHERE id=?", (round(pm, 2), round(pg, 2), c["id"]))
            need = _need_of(con, c)
            if need is None:
                continue                                                # no declared floor known: never lowered
            sp = sig_peak(con, c["rule"], c["sig"])
            need = (max(need[0], sp[0] * LEARN_MARGIN), max(need[1], sp[1] * LEARN_MARGIN))
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(c["granted"])).total_seconds()
            except (TypeError, ValueError):
                age = 0.0
            new = resize(con, c["id"], pm, pg, need[0], need[1], age)
            if new:
                out.append(f"claim #{c['id']} {c['rule']} resized: " + ", ".join(f"{d} {v:g} GB" for d, v in new.items()))
    cap = cap or budget()
    if cap.get("gpu"):
        real = sum(gpu_by_pid().values())
        waiting = con.execute("SELECT * FROM resource_claim WHERE state='waiting' AND gpu_gb>0 AND (note IS NULL OR note NOT LIKE '%idle-gpu%')").fetchall()
        for w in waiting:
            try:
                waited = (dt.datetime.now() - dt.datetime.fromisoformat(w["requested"])).total_seconds()
            except (TypeError, ValueError):
                continue
            if waited >= IDLE_WAIT_S and real < cap["gpu"] * 0.5:
                held = con.execute("SELECT COALESCE(SUM(gpu_gb),0) FROM resource_claim WHERE state='granted'").fetchone()[0]
                with tx(con):
                    con.execute("UPDATE resource_claim SET note=COALESCE(note,'') || ' idle-gpu' WHERE id=?", (w["id"],))
                    _batch_event(con, w["batch_id"], "resource_idle_wait",
                                 f"#{w['id']} {w['rule']} waited {waited / 60:.0f} min for {w['gpu_gb']:g} GB while the GPU used "
                                 f"{real:.1f} GB (claims held {held:g} of {cap['gpu']:.1f} GB)")
                out.append(f"claim #{w['id']} {w['rule']} waits on an idle GPU ({real:.1f} GB used)")
    return out


def release(con, claim_id: int, note: str = "") -> None:
    with tx(con):
        con.execute("UPDATE resource_claim SET state='released', released=?, note=COALESCE(note,'') || ? "
                    "WHERE id=? AND state!='released'", (now(), f" {note}" if note else "", claim_id))


def release_stage(con, batch: str, stage: str) -> None:
    ensure(con)
    with tx(con):
        con.execute("UPDATE resource_claim SET state='released', released=? WHERE batch_id=? AND stage=? AND pid IS NULL"
                    " AND state!='released'", (now(), batch, stage))


def release_owner(con, owner_pid: int) -> None:
    ensure(con)
    with tx(con):
        con.execute("UPDATE resource_claim SET state='released', released=? WHERE (owner_pid=? OR pid=?) AND state!='released'",
                    (now(), owner_pid, owner_pid))


def _waiting_notice(con, batch: str, rule: str, why: str) -> None:
    from . import notify
    with tx(con):
        _batch_event(con, batch, "resource_wait", f"{rule}: {why}")
    notify.send(con, f"resource_wait:{batch}:{rule}", "info", f"{batch}: {rule} waits for resources", why, cooldown_min=30)


def acquire(con, batch: str, stage: str | None, rule: dict, owner_pid: int | None, poll_s: float | None = None,
            timeout_s: float | None = None) -> int:
    """Block until granted; returns the claim id. A claim whose owner dies while it waits is released by cleanup()."""
    cid = request(con, batch, stage, rule, owner_pid=owner_pid)
    poll_s = poll_s or config()["resources"]["poll_s"] * 2
    told, t0 = False, time.time()
    try:
        while True:
            ok, why = try_grant(con, cid)
            if ok:
                return cid
            if not told:
                _waiting_notice(con, batch, rule["name"], why)
                print(f"reins: {batch}/{stage or rule['name']} waits for resources: {why} (claim #{cid})", flush=True)
                told = True
            if timeout_s is not None and time.time() - t0 > timeout_s:
                raise ReinsError(f"claim #{cid} not granted within {timeout_s:g} s: {why}")
            time.sleep(poll_s)
    except BaseException:
        release(con, cid, "gave up waiting")
        raise


def admit_stage(con, batch: str, stage: str, rs: list[dict], owner_pid: int | None) -> int | None:
    r = stage_rule(rs, stage)
    if not r:
        return None
    ensure(con)
    held = con.execute("SELECT id FROM resource_claim WHERE batch_id=? AND stage=? AND pid IS NULL AND state='granted'",
                       (batch, stage)).fetchone()
    return held[0] if held else acquire(con, batch, stage, r, owner_pid)


def live(con) -> list[dict]:
    cleanup(con)
    return [dict(r) for r in con.execute("SELECT * FROM resource_claim WHERE state!='released' ORDER BY id")]


def view(con) -> dict:
    cap = budget()
    rows = live(con)
    g = [r for r in rows if r["state"] == "granted"]
    return {"budget": cap, "held": {"mem": sum(r["mem_gb"] for r in g), "gpu": sum(r["gpu_gb"] for r in g)},
            "claims": rows}


# ---------------------------------------------------------------- process tree (used by the supervisor)

def _proc_table() -> dict[int, tuple[int, int, str]]:
    """pid -> (ppid, pgrp, command line) for every process we can read."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
            fields = stat[stat.rindex(")") + 2:].split()
            if fields[0] == "Z":
                continue
            cmd = Path(f"/proc/{d}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            out[int(d)] = (int(fields[1]), int(fields[2]), cmd)
        except (OSError, ValueError, IndexError):
            pass
    return out


def tree(root: int, table: dict | None = None) -> dict[int, tuple[int | None, str]]:
    """Every live process under `root` (by parent links, plus its process group): pid -> (parent pid, command line)."""
    t = table if table is not None else _proc_table()
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in t.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = {}, [root]
    while todo:
        p = todo.pop()
        if p in out or p not in t:
            continue
        out[p] = (t[p][0], t[p][2])
        todo.extend(kids.get(p, []))
    for pid, (ppid, pgrp, cmd) in t.items():
        if pgrp == root and pid not in out:
            out[pid] = (ppid, cmd)
    return out


def _subtree(pids: dict[int, tuple[int | None, str]], top: int) -> list[int]:
    out, todo = [], [top]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(c for c, (pp, _) in pids.items() if pp == p and c not in out)
    return out


PEAK_EVERY_S = 5.0


def rss_gb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def gpu_by_pid() -> dict[int, float]:
    """GPU memory per process (GB), from nvidia-smi; {} without a GPU."""
    try:
        q = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out: dict[int, float] = {}
    for line in q.stdout.splitlines():
        try:
            pid, mb = (x.strip() for x in line.split(","))
            out[int(pid)] = out.get(int(pid), 0.0) + float(mb) / 1024
        except ValueError:
            continue
    return out


def _signal(pids: list[int], sig) -> None:
    for p in pids:
        try:
            os.kill(p, sig)
        except (ProcessLookupError, PermissionError):
            pass


_REINS_CMD = re.compile(r"(^|\s)(-m reins(\.\w+)?|\S*bin/reins)(\s|$)")


def _is_reins(cmd: str) -> bool:
    """reins' own commands (stage-start, mark, ...) in the tree never claim anything."""
    return bool(_REINS_CMD.search(cmd))


def _die_with_parent() -> None:
    """The log follower ends when its supervisor does, even on SIGKILL (Linux prctl PR_SET_PDEATHSIG)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    except (OSError, AttributeError):
        pass


class Monitor(threading.Thread):
    """Lives in a supervisor next to the command it runs. Every poll_s: claims for new processes that match a rule
    (a claim that does not fit holds the process, SIGSTOP, until it does); kill lines from the kill log that name a pid
    seen in the tree -> on_kill(pid, cmd, line)."""

    def __init__(self, batch: str, stage: str, root: int, rs: list[dict], on_kill, cfg: dict | None = None):
        super().__init__(daemon=True)
        self.batch, self.stage, self.root, self.on_kill = batch, stage, root, on_kill
        self.match = [(r, re.compile(r["match"])) for r in rs if r.get("match")]
        cfg = cfg or config()
        self.poll = cfg["resources"]["poll_s"]
        self.kw = cfg["kill_watch"]
        self.stop_ev = threading.Event()
        self.claims: dict[int, int] = {}           # pid -> claim id
        self.held: set[int] = set()                # pids stopped because their claim waits
        self.seen: dict[int, str] = {}             # every pid ever in the tree -> command line
        self.kill_lines: list[tuple[int, str]] = []
        self.lock = threading.Lock()
        self.follower = None
        self.peaks: dict[int, list] = {}           # claimed pid -> [rule, mem GB, gpu GB, share, first seen, sig,
                                                   #                 declared mem GB, declared gpu GB (this process)]
        self.last_sample = 0.0
        self.con = None                            # set by step(): _sample resizes claims through it

    # the kill log is followed in its own thread; lines only queue here
    def _follow(self) -> None:
        pat = re.compile(self.kw["pattern"])
        try:
            self.follower = subprocess.Popen(self.kw["cmd"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True,
                                             preexec_fn=_die_with_parent)
        except OSError:
            return
        for line in self.follower.stdout:
            m = pat.search(line)
            if m:
                with self.lock:
                    self.kill_lines.append((int(m.group("pid")), line.strip()))

    def run(self) -> None:
        if self.kw.get("cmd"):
            threading.Thread(target=self._follow, daemon=True).start()
        con = connect()
        while not self.stop_ev.is_set():
            try:
                self.step(con)
            except Exception as e:                                          # noqa: BLE001  never take the run down
                print(f"reins monitor: {e}", flush=True)
            self.stop_ev.wait(self.poll)
        try:
            self.step(con)                                                  # a kill line that came in at the very end
        except Exception:                                                   # noqa: BLE001
            pass

    def stop(self) -> None:
        self.stop_ev.set()
        self.join(timeout=10)
        if self.follower and self.follower.poll() is None:
            try:
                os.killpg(self.follower.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def step(self, con) -> None:
        pids = tree(self.root)
        for p, (_, cmd) in pids.items():
            self.seen.setdefault(p, cmd)
        with self.lock:
            lines, self.kill_lines = self.kill_lines, []
        for pid, line in lines:
            if pid in self.seen:
                self.on_kill(pid, self.seen[pid], line)
        if self.match:
            self.con = con
            self._admit(con, pids)
            if time.time() - self.last_sample >= PEAK_EVERY_S:
                self.last_sample = time.time()
                self._sample(pids)

    def _sample(self, pids: dict) -> None:
        """Peak RAM / GPU of every claimed process tree, recorded when it ends (resource_peak): claims learn from it."""
        gm = gpu_by_pid() if any(r.get("gpu_gb") or r.get("split_env") for r, _ in self.match) else {}
        for p in list(self.claims):
            if p not in pids or p in self.held:
                continue
            sub = _subtree(pids, p)
            mem = sum(rss_gb(q) for q in sub)
            gpu = sum(gm.get(q, 0.0) for q in sub)
            pk = self.peaks.get(p)
            if pk:
                pk[1], pk[2] = max(pk[1], mem), max(pk[2], gpu)
                if self.con is not None and len(pk) >= 8:
                    try:                                                   # the claim follows the process
                        resize(self.con, self.claims[p], pk[1], pk[2], pk[6], pk[7], time.time() - pk[4])
                    except Exception as e:                                 # noqa: BLE001  bookkeeping never stops a run
                        print(f"reins monitor: resize: {e}", flush=True)

    def _record_peak(self, con, p: int) -> None:
        pk = self.peaks.pop(p, None)
        if not pk or (pk[1] <= 0 and pk[2] <= 0):
            return
        with tx(con):
            con.execute("INSERT INTO resource_peak (at, batch_id, rule, pid, mem_gb, gpu_gb, share, seconds, sig)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (now(), self.batch, pk[0], p, round(pk[1], 2), round(pk[2], 2), pk[3], round(time.time() - pk[4], 1),
                         (pk[5] if len(pk) > 5 else "") or None))

    def _admit(self, con, pids: dict) -> None:
        for p in [p for p in self.claims if p not in pids]:              # gone: its claim ends
            release(con, self.claims.pop(p), "process ended")
            self.held.discard(p)
            self._record_peak(con, p)
        for p, (ppid, cmd) in sorted(pids.items()):
            if p in self.claims or _is_reins(cmd):
                continue
            for r, rx in self.match:
                if not rx.search(cmd):
                    continue
                anc, dup = ppid, False                                    # a fork / child of a claimed process
                while anc in pids:                                        # is counted once, under its ancestor
                    if anc in self.claims and rx.search(pids[anc][1]):
                        dup = True
                        break
                    anc = pids[anc][0]
                if dup:
                    break
                cid = request(con, self.batch, self.stage, r, pid=p, owner_pid=p, cmd=cmd)
                self.claims[p] = cid
                n, sg = share_of(r, p), sig_of(r, cmd)
                sp = sig_peak(con, r["name"], sg)                         # resize floor: declared or own history
                self.peaks[p] = [r["name"], 0.0, 0.0, n, time.time(), sg, max(r.get("mem_gb") or 0.0, sp[0] * LEARN_MARGIN),
                                 max((r.get("gpu_gb") or 0.0) / n, sp[1] * LEARN_MARGIN)]
                ok, why = try_grant(con, cid)
                if not ok:
                    _signal(_subtree(pids, p), signal.SIGSTOP)
                    self.held.add(p)
                    _waiting_notice(con, self.batch, r["name"], f"pid {p} held: {why}")
                break
        status = con.execute("SELECT status FROM batch WHERE batch_id=?", (self.batch,)).fetchone()
        if status and status[0] != "running":                            # paused by a person or for a kill: leave it
            return
        for p in sorted(self.held, key=lambda p: self.claims.get(p, 0)):
            ok, _ = try_grant(con, self.claims[p])
            _signal(_subtree(pids, p), signal.SIGCONT if ok else signal.SIGSTOP)   # re-stop after a ctl resume
            if ok:
                self.held.discard(p)
