#!/usr/bin/env python3
"""$E2E_CONTAINER_HOOK of a reins run: e2e's tools/container.sh calls it before `exec docker run`.

    python3 container_claim.py PID GPU_GB MEM_GB

The steps inside the container are invisible to reins' per-step GPU / RAM claims, so the whole container holds one
claim (its largest step: georef ~13 GB GPU), owned by the calling shell, which exec()s docker and keeps its pid -- the
claim ends when the container does."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from reins import resources as R  # noqa: E402
from reins.store import connect  # noqa: E402

if __name__ == "__main__":
    pid, gpu, mem = int(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
    if os.environ.get("REINS_BATCH"):
        R.acquire(connect(), os.environ["REINS_BATCH"], os.environ.get("REINS_STAGE"),
                  {"name": "container", "mem_gb": mem, "gpu_gb": gpu, "tags": [], "match": None, "stage": None,
                   "split_env": None}, owner_pid=pid)
