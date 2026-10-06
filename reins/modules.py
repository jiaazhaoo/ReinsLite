"""C2: modules and their versions. candidate -> released (only with gate evidence) -> retired."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import names
from .store import ReinsError, now, session, today, tx


def _git(cwd: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def add(con, name: str, project: str, about: str) -> None:
    names.check_token("module", name)
    with tx(con):
        if con.execute("SELECT 1 FROM module WHERE name=?", (name,)).fetchone():
            raise ReinsError(f"module {name!r} already exists (module names are global)")
        con.execute("INSERT INTO module VALUES (?,?,?,?)", (name, project, about, now()))


def _event(con, version: str, event: str, detail: str | None = None) -> None:
    con.execute("INSERT INTO module_event (at, version, event, detail, session) VALUES (?,?,?,?,?)",
                (now(), version, event, detail, session()))


def new(con, module: str, suffix: str, about: str, cwd: Path, pins: dict | None = None,
        day: str | None = None) -> str:
    """Allocate <module>-<suffix>-<day>-<n>: n counts this module's versions that day, from 1."""
    day = day or today()
    commit, branch, worktree = _git(cwd, "rev-parse", "HEAD"), _git(cwd, "branch", "--show-current"), \
        _git(cwd, "rev-parse", "--show-toplevel")
    with tx(con):
        if not con.execute("SELECT 1 FROM module WHERE name=?", (module,)).fetchone():
            raise ReinsError(f"module {module!r} is not registered; run: reins module add {module} --project ... --about ...")
        seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM module_version WHERE module=? AND day=?",
                          (module, day)).fetchone()[0]
        version = names.module_version(module, suffix, day, seq)
        con.execute("INSERT INTO module_version (version, module, suffix, day, seq, status, about, commit_sha, branch,"
                    " worktree, session, pins, created) VALUES (?,?,?,?,?, 'candidate', ?,?,?,?,?,?,?)",
                    (version, module, suffix, day, seq, about, commit, branch, worktree, session(),
                     json.dumps(pins or {}, sort_keys=True), now()))
        _event(con, version, "created", about)
    return version


def get(con, version: str):
    names.parse_module_version(version)
    row = con.execute("SELECT * FROM module_version WHERE version=?", (version,)).fetchone()
    if not row:
        raise ReinsError(f"module version {version!r} is not registered")
    return row


def release(con, version: str, gate: str) -> None:
    if not gate.strip():
        raise ReinsError("release needs gate evidence (what was run, on which benchmark, result)")
    with tx(con):
        row = get(con, version)
        if row["status"] != "candidate":
            raise ReinsError(f"{version} is {row['status']}; only a candidate can be released")
        con.execute("UPDATE module_version SET status='released' WHERE version=?", (version,))
        _event(con, version, "released", gate)


def retire(con, version: str, why: str) -> None:
    with tx(con):
        row = get(con, version)
        if row["status"] == "retired":
            raise ReinsError(f"{version} is already retired")
        con.execute("UPDATE module_version SET status='retired' WHERE version=?", (version,))
        _event(con, version, "retired", why)


def note(con, version: str, event: str, detail: str) -> None:
    """Free event on a version: a gate result, a status line from the developing session."""
    with tx(con):
        get(con, version)
        _event(con, version, event, detail)


def status(con, module: str) -> dict:
    """What runs in production (newest released) and what is being developed (candidates)."""
    if not con.execute("SELECT 1 FROM module WHERE name=?", (module,)).fetchone():
        raise ReinsError(f"module {module!r} is not registered")
    rows = con.execute("SELECT * FROM module_version WHERE module=? ORDER BY day, seq", (module,)).fetchall()
    released = [r for r in rows if r["status"] == "released"]
    return {"module": module,
            "production": dict(released[-1]) if released else None,
            "candidates": [dict(r) for r in rows if r["status"] == "candidate"]}
