"""Input health check before a batch runs. Built-in checks plus the project's own ([[preflight]] in reins.toml).

    reins preflight BATCH [--project-root DIR]

A production or rework batch cannot start its first stage until preflight passed (recorded on the batch).
Project checks are commands run with REINS_BATCH, REINS_CASES (path of the case list) and REINS_WORK_DIR set;
exit 0 = pass, anything printed is the detail.

    [[preflight]]
    name = "mapping_fresh"
    cmd = "python3 tools/check_mapping_fresh.py"
    [[preflight]]
    name = "scan_coverage"
    cmd = "python3 tools/check_scans.py --min 0.95"
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from . import batches, envs
from .store import ReinsError, config, now, tx


def builtin(con, b) -> list[tuple[str, bool, str]]:
    out = []
    bid = b["batch_id"]
    cases = Path(b["case_set_path"])
    out.append(("case_set_present", cases.exists(), str(cases)))
    stages = con.execute("SELECT * FROM batch_stage WHERE batch_id=?", (bid,)).fetchall()
    if any(s["paid"] for s in stages):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{config()['gateway_port']}/health", timeout=3)
            out.append(("gateway_up", True, ""))
        except Exception as e:                                                  # noqa: BLE001
            out.append(("gateway_up", False, f"paid stages planned but the gateway is down: reins gateway serve ({e})"))
        out.append(("spend_cap_set", b["spend_cap"] > 0, f"cap ${b['spend_cap']:.2f}"))
    if b["type"] in batches.STRICT_TYPES:
        rel = con.execute("SELECT worktree FROM release WHERE name=?", (b["release_name"],)).fetchone()
        out.append(("release_tree", bool(rel and Path(rel["worktree"]).is_dir()), b["release_name"] or "no release"))
        out.append(("env_recorded", bool(b["env_name"]), b["env_name"] or "pass --env when opening a production batch"))
        out.append(("ruleset_recorded", bool(b["ruleset"]), b["ruleset"] or "pass --ruleset when opening a production batch"))
    if b["env_name"]:
        try:
            envs.get(con, b["env_name"]); out.append(("env_exists", True, b["env_name"]))
        except ReinsError as e:
            out.append(("env_exists", False, str(e)))
    for path in {"/data", str(Path(b["work_dir"]).resolve()) if b["work_dir"] else "/data"}:
        if Path(path).exists():
            u = shutil.disk_usage(path)
            pct = 100 * u.used / u.total
            out.append((f"disk_{path.strip('/').replace('/', '_') or 'root'}", pct < config()["disk_pause_pct"], f"{pct:.0f}% used"))
    if b["work_dir"]:
        wd = Path(b["work_dir"])
        out.append(("work_dir_writable", wd.exists() and os.access(wd, os.W_OK) or not wd.exists(), str(wd)))
    return out


def project_checks(b, root: Path | None) -> list[tuple[str, bool, str]]:
    import tomllib
    if not root or not (root / "reins.toml").is_file():
        return []
    cfg = tomllib.loads((root / "reins.toml").read_text(encoding="utf-8"))
    out = []
    env = {**os.environ, "REINS_BATCH": b["batch_id"], "REINS_CASES": b["case_set_path"], "REINS_WORK_DIR": b["work_dir"] or ""}
    for c in cfg.get("preflight", []):
        r = subprocess.run(c["cmd"], shell=True, cwd=str(root), env=env, capture_output=True, text=True, timeout=600)
        out.append((c["name"], r.returncode == 0, (r.stdout + r.stderr).strip()[-300:]))
    return out


def run(con, batch: str, root: Path | None = None) -> dict:
    b = batches.get(con, batch)
    results = builtin(con, b) + project_checks(b, root)
    ok = all(r[1] for r in results)
    with tx(con):
        con.executemany("INSERT INTO preflight (at, batch_id, check_name, ok, detail) VALUES (?,?,?,?,?)",
                        [(now(), batch, n, int(o), d) for n, o, d in results])
        con.execute("UPDATE batch SET preflight_ok=? WHERE batch_id=?", (int(ok), batch))
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?,?,?)",
                    (now(), batch, "preflight", f"{'pass' if ok else 'FAIL'}: {sum(1 for r in results if not r[1])} failing of {len(results)}"))
    return {"ok": ok, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in results]}
