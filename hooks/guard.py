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


def managed_repo(where: str) -> bool:
    """True when the directory belongs to a repo managed by reins (its main repo has reins.toml). Other repos,
    ReinsLite itself included, keep their own rules."""
    p = Path(where)
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


def merges_main(cmd: str) -> str | None:
    """The directory a git segment merges or pushes in (following `cd` and `git -C`), or None.
    Decided by the programs run, not by words in the command."""
    from reins.sessions import segments
    cwd = Path(os.getcwd())
    for seg in segments(cmd):
        a = seg["argv"]
        if a[0] == "cd" and len(a) > 1:
            cwd = (cwd / os.path.expanduser(a[1])).resolve()
            continue
        if Path(a[0]).name != "git":
            continue
        rest, where = a[1:], cwd
        if "-C" in rest:
            i = rest.index("-C")
            where = (cwd / rest[i + 1]).resolve() if i + 1 < len(rest) else cwd
            rest = rest[:i] + rest[i + 2:]
        args = [x for x in rest if not x.startswith("-")]
        if args and ((args[0] == "push" and "--dry-run" not in rest) or args[0] == "merge"):
            return str(where)                        # merging is done by reins dev finish in a managed repo
    return None


_PROJECTS: list[tuple[Path, dict]] | None = None


def projects() -> list[tuple[Path, dict]]:
    """(repo, reins.toml) of every managed project under /env/code."""
    global _PROJECTS
    if _PROJECTS is None:
        import tomllib
        _PROJECTS = []
        for f in Path(os.environ.get("REINS_CODE_ROOT", "/env/code")).glob("*/reins.toml"):
            if not (f.parent / ".git").is_dir():                # a linked worktree is a checkout, not the project
                continue
            try:
                _PROJECTS.append((f.parent, tomllib.loads(f.read_text(encoding="utf-8"))))
            except Exception:                                               # noqa: BLE001
                pass
    return _PROJECTS


def pipeline_programs() -> set[str]:
    out = set()
    for _, cfg in projects():
        out |= set((cfg.get("run") or {}).get("programs", []))
    return out


def blocked_tools() -> set[str]:
    out = set()
    for _, cfg in projects():
        out |= set((cfg.get("dev") or {}).get("blocked_tools", []))
    return out


def _script_runs(path: str, progs: set[str], depth: int = 0) -> bool:
    """A wrapper script that executes a pipeline program (resume_*.sh calling run_local_qa.sh)."""
    from reins.sessions import segments
    p = Path(path)
    if depth > 2 or not p.is_file() or p.stat().st_size > 200_000:
        return False
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        return False
    for line in text.splitlines():
        for seg in segments(line):
            a = seg["argv"]
            names = [Path(a[0]).name] + ([Path(a[1]).name] if len(a) > 1 and re.match(r"^(python[0-9.]*|\S*/python[0-9.]*|bash|sh|\$\{?\w*PY\}?)$", a[0]) else [])
            if set(names) & progs:
                return True
            if a[0].endswith(".sh") and _script_runs(str((p.parent / a[0]).resolve()), progs, depth + 1):
                return True
    return False


def runs_pipeline_directly(cmd: str) -> str | None:
    """The program a session would run by hand that only reins may run; None when the command goes through reins."""
    from reins.sessions import segments
    progs = pipeline_programs()
    if not progs:
        return None
    for seg in segments(cmd):
        a = seg["argv"]
        if Path(a[0]).name == "reins" or (len(a) > 2 and a[1:3] == ["-m", "reins"]):
            return None                                     # reins run ... -- PROGRAM: the supervisor runs it
        interp = re.match(r"^(python[0-9.]*|\S*/python[0-9.]*|bash|sh|\$\{?\w*PY\}?)$", a[0])
        target = a[1] if interp and len(a) > 1 else a[0]
        if interp and len(a) > 2 and a[1] == "-m":
            target = a[2]
        if Path(target).name in progs or target in progs:
            return Path(target).name
        if target.endswith(".sh") and _script_runs(os.path.abspath(target), progs):
            return f"{target} (it runs a pipeline program)"
    return None


def legacy_tool(cmd: str) -> str | None:
    from reins.sessions import segments
    tools = blocked_tools()
    for seg in segments(cmd):
        a = seg["argv"]
        for t in a[:2]:
            if any(t.endswith(b) for b in tools):
                verb = a[a.index(t) + 1] if a.index(t) + 1 < len(a) else ""
                if verb not in ("list", "--help", "-h", ""):
                    return t
    return None


def managed_main_or_unheld(path: str) -> str | None:
    """In a managed repo, files change only in a worktree this session holds as a candidate version."""
    p = Path(os.path.abspath(path))
    for repo, cfg in projects():
        rp = repo.resolve()
        if p == rp or rp in p.parents:
            return f"{p} is in {repo.name}'s main worktree: start a candidate first: reins dev start MODULE SUFFIX --about '...'"
    for d in p.parents:
        g = d / ".git"
        if g.is_file():
            try:
                gd = g.read_text().split(":", 1)[1].strip()
                main = Path(gd).parents[2] if "/worktrees/" in gd else d
            except (OSError, IndexError):
                return None
            if not (main / "reins.toml").is_file():
                return None
            if "-rel-" in d.name:
                return None                                 # frozen() reports release trees
            me = os.environ.get("REINS_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID")
            db = HOME / "reins.db"
            if db.exists():
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                row = con.execute("SELECT holder FROM lease WHERE resource=?", (f"worktree:{d}",)).fetchone()
                if row and row[0] == me:
                    return None
                if row:
                    return None                             # other_sessions_worktree() reports it
            return (f"{d} is not a registered candidate of this session: "
                    f"reins dev adopt {d} MODULE SUFFIX --about '...'  (or reins dev start for a new branch)")
        if g.is_dir():
            return None
    return None


def check_bash(cmd: str) -> str | None:
    from reins.sessions import detached_pipeline        # by the programs executed, not words in the command
    prog = runs_pipeline_directly(cmd)
    if prog:
        return (f"{prog} is a pipeline program: it runs only through reins, which registers the batch, its cost and "
                f"time. Open the batch (reins batch open ...), then: reins run BATCH --self-staged -- COMMAND")
    tool = legacy_tool(cmd)
    if tool:
        return f"{tool} is replaced by reins in this project: reins dev start / finish / release"
    if detached_pipeline(cmd):
        return "pipeline commands are not started detached by hand. Use: reins run BATCH STAGE -- CMD... " \
               "(supervised, pgid-controlled, survives this session)"
    where = merges_main(cmd)
    if where and managed_repo(where):
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
    return managed_main_or_unheld(p)


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
