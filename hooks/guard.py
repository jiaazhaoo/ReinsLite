#!/usr/bin/env python3
"""Claude Code PreToolUse guard: the guard-rail half of C7/C10 (the lock half is leases, the gateway and read-only dirs).

Blocks, with the reins command to use instead:
  Bash   setsid / nohup / disown / trailing &  on a pipeline command  -> reins run BATCH STAGE -- CMD
         git merge|push into main, git checkout/switch inside another session's worktree -> reins dev finish
         writes (> >> tee cp mv rm chmod) into frozen paths: /data/benchmarks/*, *-rel-*, release worktrees
  Edit/Write/MultiEdit   files in frozen paths or in a worktree leased by another session

Install (in ~/.claude/settings.json):
  "hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit",
             "hooks": [{"type": "command", "command": "python3 /env/code/ReinsLite/hooks/guard.py"}]}]}
Exit 2 = blocked (stderr is shown to the model); exit 0 = allowed. Any internal error allows (fail open) and
logs to $REINS_HOME/guard.log, so a broken guard never stops work silently.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HOME = Path(os.environ.get("REINS_HOME", "/data/reins"))
FROZEN = [re.compile(p) for p in (r"^/data/benchmarks/", r"/[^/ ]+-rel-[^/ ]+/", r"^/data/reins/cache/",
                                  r"^/data/[^/]+/(text|spatial)/delivery/")]
SWITCH = re.compile(r"git\b[^|;&]*\b(checkout|switch)\b")
WRITE = re.compile(r"(>>?|\btee\b|\bcp\b|\bmv\b|\brm\b|\bchmod\b|\bmkdir\b|\btouch\b)\s+(-\w+\s+)*(\S+)")


def _registry_frozen() -> list[str]:
    """Paths the registry knows are frozen: frozen benchmarks, release trees, registered deliverables."""
    db = HOME / "reins.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        out = [r[0] for r in con.execute("SELECT path FROM benchmark WHERE status='frozen'")]
        out += [r[0] for r in con.execute("SELECT worktree FROM release")]
        out += [r[0] for r in con.execute("SELECT path FROM deliverable")]
        return out
    except sqlite3.Error:
        return []


def frozen(path: str) -> bool:
    if any(p.search(path) for p in FROZEN):
        return True
    return any(path == f or path.startswith(f.rstrip("/") + "/") for f in _registry_frozen())


def other_sessions_worktree(path: str) -> str | None:
    me = os.environ.get("REINS_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    db = HOME / "reins.db"
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    for res, holder in con.execute("SELECT resource, holder FROM lease WHERE resource LIKE 'worktree:%'"):
        wt = res.split(":", 1)[1]
        if (path == wt or path.startswith(wt.rstrip("/") + "/")) and holder != me:
            return f"{wt} (session {holder})"
    return None


GIT_C = re.compile(r"git\s+-C\s+(\S+)")


def managed_repo(cmd: str) -> bool:
    """True when the git command acts on a repo managed by reins (its main repo has reins.toml). Other repos,
    ReinsLite itself included, keep their own rules."""
    m = GIT_C.search(cmd)
    p = Path(m.group(1).strip("'\"")) if m else Path(os.getcwd())
    for d in (p, *p.parents):
        g = d / ".git"
        if g.is_dir():
            return (d / "reins.toml").is_file()
        if g.is_file():
            try:
                gd = g.read_text().split(":", 1)[1].strip()
                main = Path(gd).parents[2] if "/worktrees/" in gd else d
            except (OSError, IndexError):
                main = d
            return (main / "reins.toml").is_file()
    return False


def merges_main(cmd: str) -> bool:
    """A git segment that merges into or pushes main (by the program run, not by words in the command)."""
    from reins.sessions import segments
    for seg in segments(cmd):
        a = seg["argv"]
        if Path(a[0]).name != "git":
            continue
        rest = a[1:]
        if "-C" in rest:
            i = rest.index("-C")
            rest = rest[:i] + rest[i + 2:]
        args = [x for x in rest if not x.startswith("-")]
        if args and args[0] == "push" and "--dry-run" not in rest:
            return True
        if args and args[0] == "merge":
            return True                              # merging is done by reins dev finish in a managed repo
    return False


def check_bash(cmd: str) -> str | None:
    from reins.sessions import detached_pipeline        # by the programs executed, not words in the command
    if detached_pipeline(cmd):
        return "pipeline commands are not started detached by hand. Use: reins run BATCH STAGE -- CMD... " \
               "(supervised, pgid-controlled, survives this session)"
    if merges_main(cmd) and managed_repo(cmd):
        return "merging into or pushing main is done by `reins dev finish VERSION` after the gate, never by hand"
    cwd = os.getcwd()
    if SWITCH.search(cmd):
        o = other_sessions_worktree(cwd)
        if o or frozen(cwd + "/"):
            return f"no branch switching inside {o or cwd}: it is a release/batch tree or another session's worktree"
    for m in WRITE.finditer(cmd):
        target = os.path.abspath(os.path.join(cwd, m.group(3).strip("'\"")))
        if frozen(target):
            return f"{target} is frozen (benchmark / release / delivery). Create a new version instead of changing it"
        o = other_sessions_worktree(target)
        if o:
            return f"{target} is inside {o}; start your own: reins dev start MODULE SUFFIX --about ..."
    return None


def check_edit(path: str) -> str | None:
    p = os.path.abspath(path)
    if frozen(p):
        return f"{p} is frozen (benchmark / release / delivery). Create a new version instead of changing it"
    o = other_sessions_worktree(p)
    if o:
        return f"{p} is inside {o}; start your own: reins dev start MODULE SUFFIX --about ..."
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        tool, inp = data.get("tool_name", ""), data.get("tool_input", {}) or {}
        if tool == "Bash":
            msg = check_bash(inp.get("command", ""))
        elif tool in ("Edit", "Write", "MultiEdit"):
            msg = check_edit(inp.get("file_path", ""))
        else:
            msg = None
        if msg:
            print(f"reins guard: {msg}", file=sys.stderr)
            return 2
        return 0
    except Exception as e:                                                      # noqa: BLE001
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            with (HOME / "guard.log").open("a") as f:
                f.write(f"guard error: {e!r}\n")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
