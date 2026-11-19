#!/usr/bin/env python3
"""e2e-plan-extract's run-control interface, served by reins.

e2e knows nothing of reins. Where its code needs a run registered (intake opens a run, marks its stages, asks for the
key of a paid stage, leaves a note), it calls the command in $E2E_RUN_CONTROL; without it the code runs on its own
(e2e_plan_extract/runctl.py, local mode). On this machine $E2E_RUN_CONTROL points here, so every run is a reins batch:

    E2E_RUN_CONTROL="python3 /env/code/ReinsLite/adapters/e2e/runctl.py"

Verbs (e2e's words -> reins):
    open  --project P --scope S --kind K --purpose T --cases F --work-dir W [--input F]... [--parent R] [--stage X]...
          -> reins batch open ... --type K; prints the run id
    launch RUN --cwd DIR -- ARGV...          -> reins run RUN --self-staged --cwd DIR -- ARGV
    stage-start RUN STAGE [ARGS]             -> reins batch stage-start
    mark RUN STAGE FILE                      -> reins batch mark RUN STAGE --file FILE (case_id, status, reason)
    stage-end RUN STAGE [ARGS]               -> reins batch stage-end
    skip RUN STAGE --why W                   -> reins batch skip-stage
    status RUN                               -> {"status", "stages": [names], "spend_cap"} as JSON
    key RUN STAGE                            -> the API key a paid stage's calls carry (the gateway's batch:stage token)
    wait RUN [--timeout S]                   -> reins wait
    notify KEY LEVEL TITLE BODY              -> a note on the board (reins notify)

reins is called from / so that the intake project's batches are not mistaken for e2e's (no settings above /);
`open --project P` for a project reins manages (e2e-plan-extract: e.g. tools/cloud/prepare.sh's chunk runs) runs from
that project's checkout, so the batch gets its rules (work dirs, case ids, stages).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REINS = os.environ.get("REINS_BIN") or str(ROOT / "bin" / "reins")


def reins(*a: str, check: bool = True, cwd: str = "/") -> str:
    p = subprocess.run([REINS, *a], capture_output=True, text=True, cwd=cwd)
    if check and p.returncode:
        sys.stderr.write(p.stderr)
        raise SystemExit(p.returncode or 1)
    sys.stderr.write(p.stderr)
    return p.stdout


def _open(args: list[str]) -> None:
    args = ["--type" if a == "--kind" else a for a in args]
    cwd = "/"
    if "--project" in args:
        sys.path.insert(0, str(ROOT))
        from reins import projects
        name = args[args.index("--project") + 1]
        hit = next((r for r, c in projects.all_() if c.get("project") == name), None)
        if hit:
            cwd = str(hit)
            i = args.index("--project"); args = args[:i] + args[i + 2:]     # the checkout names the project
    out = reins("batch", "open", *args, cwd=cwd)
    scope = args[args.index("--scope") + 1] if "--scope" in args else ""
    kind = args[args.index("--type") + 1] if "--type" in args else ""
    m = re.search(rf"\b{re.escape(scope)}-{re.escape(kind)}-\d{{8}}-\d+\b", out) if scope and kind else None
    print(m.group(0) if m else (out.split() or [""])[0])


def _status(run: str) -> None:
    st = json.loads(reins("batch", "status", run, "--json", check=False) or "{}")
    b = st.get("batch") or {}
    print(json.dumps({"status": b.get("status"), "spend_cap": b.get("spend_cap") or 0,
                      "work_dir": b.get("work_dir"), "stages": [x.get("stage") for x in st.get("stages") or []]}))


def _notify(key: str, level: str, title: str, body: str) -> None:
    sys.path.insert(0, str(ROOT))
    from reins import notify
    from reins.store import connect
    notify.send(connect(ensure=False), key, level, title, body, cooldown_min=0)


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    v, a = argv[0], argv[1:]
    if v == "open":
        _open(a)
    elif v == "launch":
        run, rest = a[0], a[1:]
        print(reins("run", run, "--self-staged", *rest), end="")
    elif v in ("stage-start", "stage-end"):
        print(reins("batch", v, *a), end="")
    elif v == "mark":
        print(reins("batch", "mark", a[0], a[1], "--file", a[2]), end="")
    elif v == "skip":
        print(reins("batch", "skip-stage", *a), end="")
    elif v == "status":
        _status(a[0])
    elif v == "key":
        print(f"{a[0]}:{a[1]}")
    elif v == "wait":
        print(reins("wait", *a, check=False), end="")
    elif v == "notify":
        _notify(*a[:4])
    else:
        raise SystemExit(f"unknown verb {v!r}\n{__doc__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
