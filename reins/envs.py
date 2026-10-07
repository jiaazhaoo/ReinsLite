"""Environment versions: env-<name>-vN = a manifest of what the code ran in (interpreter, packages, CUDA, assets).

    reins env snapshot NAME --python /env/venv/x/bin/python [--asset PATH ...]     -> env-NAME-vN (same content = same version)
    reins env check NAME --python ... [--asset ...]                                 what differs from the latest snapshot
    reins env list

Two snapshots with the same manifest hash are the same version, so re-snapshotting a stable environment is free.
A batch records its env in `batch.env_name`; "the new env passed" is then a statement about a hash, not a memory.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

from . import names
from .store import ReinsError, now, tx


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def manifest(python: str, assets: list[str] | None = None) -> dict:
    py = _run([python, "-c", "import sys; print(sys.version.split()[0])"])
    if not py:
        raise ReinsError(f"cannot run {python}")
    pkgs = {}
    for line in _run([python, "-m", "pip", "freeze", "--disable-pip-version-check"]).splitlines():
        if "==" in line:
            k, v = line.split("==", 1); pkgs[k.lower()] = v
        elif " @ " in line:
            k, v = line.split(" @ ", 1); pkgs[k.lower()] = v
    nv = _run(["nvidia-smi", "--query-gpu=driver_version,name", "--format=csv,noheader"])
    cuda = _run([python, "-c", "import torch; print(torch.version.cuda or '')"]) or \
           _run([python, "-c", "import paddle; print(paddle.version.cuda())"])
    asset_shas = {}
    for a in assets or []:
        p = Path(a)
        if p.is_file():
            asset_shas[str(p)] = names.sha256_file(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    asset_shas[str(f)] = names.sha256_file(f)
        else:
            raise ReinsError(f"asset {a} does not exist")
    return {"python": python, "python_version": py, "platform": platform.platform(), "packages": pkgs,
            "nvidia": nv, "cuda": cuda, "assets": asset_shas}


def _sha(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def snapshot(con, base: str, python: str, assets: list[str] | None = None) -> dict:
    names.check_token("env name", base)
    m = manifest(python, assets)
    sha = _sha(m)
    with tx(con):
        same = con.execute("SELECT name FROM env WHERE base=? AND sha=?", (base, sha)).fetchone()
        if same:
            return {"name": same["name"], "new": False, "packages": len(m["packages"]), "assets": len(m["assets"])}
        v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM env WHERE base=?", (base,)).fetchone()[0]
        name = f"env-{base}-v{v}"
        con.execute("INSERT INTO env VALUES (?,?,?,?,?,?)", (name, base, v, sha, json.dumps(m, sort_keys=True), now()))
    return {"name": name, "new": True, "packages": len(m["packages"]), "assets": len(m["assets"])}


def get(con, name: str):
    row = con.execute("SELECT * FROM env WHERE name=?", (name,)).fetchone()
    if not row:
        raise ReinsError(f"env {name!r} is not registered (reins env snapshot)")
    return row


def check(con, base: str, python: str, assets: list[str] | None = None) -> dict:
    row = con.execute("SELECT * FROM env WHERE base=? ORDER BY version DESC LIMIT 1", (base,)).fetchone()
    if not row:
        raise ReinsError(f"no snapshot for env {base!r}")
    old, new = json.loads(row["manifest"]), manifest(python, assets)
    diff = {}
    for k in ("python_version", "cuda", "nvidia", "platform"):
        if old.get(k) != new.get(k):
            diff[k] = (old.get(k), new.get(k))
    for section in ("packages", "assets"):
        a, b = old.get(section, {}), new.get(section, {})
        ch = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
        if ch:
            diff[section] = ch
    return {"against": row["name"], "same": not diff, "diff": diff}


def list_(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT name, base, version, sha, created FROM env ORDER BY base, version")]
