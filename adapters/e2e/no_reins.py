#!/usr/bin/env python3
"""Part of e2e's gate (run in the candidate's worktree): e2e is business code that names no reins (user 2026-11-19:
不允许出现任何 reinslite). Once main carries no reins.toml, a candidate whose tree mentions reins anywhere is refused
-- a candidate cut from the older main must be rebased onto it first."""
import subprocess
import sys

if subprocess.run(["git", "cat-file", "-e", "main:reins.toml"], capture_output=True).returncode == 0:
    sys.exit(0)                                   # main not stripped yet: nothing to hold the candidate to
hits = subprocess.run(["git", "grep", "-il", "reins"], capture_output=True, text=True).stdout.split()
if hits:
    print(f"no_reins: {len(hits)} files mention reins (rebase onto main, then take it out): {', '.join(hits[:20])}")
    sys.exit(1)
print("no_reins: ok")
