#!/usr/bin/env python3
"""Runs one of e2e's gate scripts for `reins dev finish` and hands its result to reins.

    python3 $REINS_ADAPTER/gate.py [ENV=V ...] PROGRAM SCRIPT [ARGS...]
    e.g. python3 $REINS_ADAPTER/gate.py python3 benchmark/judge_hmlr/gate.py

e2e's gates write their result as plain JSON with `--out FILE` (missed_error / review_load / golden_regressions, or
module metrics); this passes `--out $REINS_GATE_OUT`, so reins/gate.py reads it as it is. A gate is never a batch:
the run's variables (REINS_BATCH, E2E_RUN_ID, E2E_STAGE_HOOK ...) are taken out of its environment.

reins' other gate knobs are translated into the gate's own arguments:
    REINS_GATE_SKIP=module   the plan_crop module gate is not run (reins records the skip and its reason)
    REINS_VERSION            --label VERSION (the scratch folder's name)

A candidate cut before e2e's gates took --out (it still reads $REINS_GATE_OUT itself) is run unchanged.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DROP = ("REINS_BATCH", "REINS_STAGE", "E2E_RUN_ID", "E2E_STAGE_HOOK", "E2E_STAGE_VERSIONS", "E2E_WORK_DIR",
        "E2E_RUN_CONTROL", "E2E_CONTAINER_HOOK")


def main(argv: list[str]) -> int:
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    while argv and "=" in argv[0] and not argv[0].startswith("-"):
        k, v = argv.pop(0).split("=", 1)
        env[k] = v
    if len(argv) < 2:
        raise SystemExit(__doc__)
    script = Path(argv[1])
    text = script.read_text(encoding="utf-8") if script.is_file() else ""
    new_style = '"--out"' in text
    if os.environ.get("REINS_GATE_SKIP") == "module" and "module_gate" in script.name and new_style:
        print(f"{script} skipped (--skip-tier module; the reason is recorded with the version)")
        return 0
    cmd = list(argv)
    if new_style:
        if os.environ.get("REINS_GATE_OUT"):
            cmd += ["--out", os.environ["REINS_GATE_OUT"]]
        if os.environ.get("REINS_VERSION") and '"--label"' in text:
            cmd += ["--label", os.environ["REINS_VERSION"]]
    else:
        env = {**env, **{k: os.environ[k] for k in ("REINS_GATE_OUT", "REINS_VERSION", "REINS_GATE_SKIP") if k in os.environ}}
    return subprocess.run(cmd, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
