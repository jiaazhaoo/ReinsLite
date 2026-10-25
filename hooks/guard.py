#!/usr/bin/env python3
"""Claude Code PreToolUse guard: the guard-rail half of C7/C10 (the lock half is leases, the gateway and read-only dirs).

Blocks, with the reins command to use instead:
  Bash   setsid / nohup / disown / trailing &  on a pipeline command  -> reins run BATCH STAGE -- CMD
         git merge|push into main, git checkout/switch inside another session's worktree -> reins dev finish
         writes (> >> tee cp mv rm chmod) into frozen paths: the bench root, release worktrees, config frozen_paths
         writes into another session's live batch work dir (shell targets, and script bodies that name it and write)
  Edit/Write/MultiEdit   files in frozen paths, in a worktree leased by another session, or in another session's
                         live batch work dir

Install (in ~/.claude/settings.json):
  "hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit",
             "hooks": [{"type": "command", "command": "python3 <ReinsLite>/hooks/guard.py"}]}]}
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
from reins.store import DEFAULT_HOME  # noqa: E402

HOME = Path(os.environ.get("REINS_HOME", DEFAULT_HOME))


def _frozen() -> list[re.Pattern]:
    """Frozen by pattern: release trees, the gateway cache, plus the machine's own (config.toml frozen_paths). Frozen
    benchmarks come from the registry (their version directories), so scratch space under the bench root stays usable."""
    pats = [r"/[^/ ]+-rel-[^/ ]+/"]
    try:
        from reins.store import config
        cfg = config()
        pats += ["^" + re.escape(str(HOME / "cache")) + "/"]
        pats += list(cfg.get("frozen_paths") or [])
    except Exception:                                                       # noqa: BLE001  (fail open)
        pass
    return [re.compile(p) for p in pats]


FROZEN = _frozen()
WRITE_PROGRAMS = {"tee", "cp", "mv", "rm", "chmod", "chown", "mkdir", "touch", "truncate", "ln", "rsync", "sed"}
MODE_FIRST = {"chmod", "chown"}                 # first positional is a mode / owner, the targets follow


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
    return frozen_reason(path) is not None


def frozen_reason(path: str) -> str | None:
    """Why a path may not be written, with where the write belongs instead; None when it may."""
    rel = re.search(r"^(.*/[^/ ]+-rel-[^/ ]+)(/|$)", path)
    if rel:
        tree = rel.group(1)
        dirs = _batch_dirs_for(tree)
        return (f"{tree} is a release tree: production batches run from it and it never changes. Write outputs to the "
                f"batch's work dir{' (' + ', '.join(dirs) + ')' if dirs else ' under /data'}; change code with "
                f"reins dev start MODULE SUFFIX --about ...")
    for f in _registry_frozen():
        if path == f or path.startswith(f.rstrip("/") + "/"):
            return (f"{f} is frozen (a benchmark version or a registered deliverable). Make a new version instead: "
                    f"reins bench bump NAME, or reins deliver register for a new deliverable")
    if any(p.search(path) for p in FROZEN):
        return f"{path} is in a frozen path (gateway cache or config.toml frozen_paths)"
    return None


def _batch_dirs_for(tree: str) -> list[str]:
    """Work dirs of open batches that run from this release tree (where their outputs belong)."""
    db = HOME / "reins.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rel = con.execute("SELECT name FROM release WHERE worktree=?", (tree,)).fetchone()
        if not rel:
            return []
        return [r[0] for r in con.execute("SELECT DISTINCT work_dir FROM batch WHERE release_name=? AND work_dir IS NOT NULL"
                                          " AND status IN ('open','running','paused') LIMIT 3", (rel[0],))]
    except sqlite3.Error:
        return []


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


def _my_line() -> set[str]:
    """This session and the sessions it continues (fork or context continuation): their batches are its own."""
    me = os.environ.get("REINS_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    out = {me} if me else set()
    try:
        con = sqlite3.connect(f"file:{HOME / 'reins.db'}?mode=ro", uri=True, timeout=5)
        cur = me
        for _ in range(20):
            row = con.execute("SELECT parent FROM session WHERE id=?", (cur,)).fetchone() if cur else None
            if not row or not row[0] or row[0] in out:
                break
            out.add(row[0]); cur = row[0]
    except sqlite3.Error:
        pass
    return out


def _live_batch_dirs() -> list[tuple[str, str, str]]:
    """(work dir, batch, owner) of live batches of other sessions."""
    db = HOME / "reins.db"
    if not db.exists():
        return []
    mine = _my_line()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT work_dir, batch_id, owner_session FROM batch WHERE status IN ('open','running','paused')"
                           " AND work_dir IS NOT NULL AND work_dir<>'' AND owner_session IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []
    return [(w.rstrip("/"), b, o) for w, b, o in rows if o not in mine]


def other_sessions_batch_dir(path: str) -> str | None:
    """2026-10-25: a session guessed its next batch id and wrote into another session's experiment dir."""
    for wd, b, o in _live_batch_dirs():
        if path == wd or path.startswith(wd + "/"):
            return f"{wd} is the work dir of {b} (session {o[:8]})"
    return None


SCRIPT_WRITE = re.compile(r"""open\([^)]*['"][wa]b?['"]|write_text|write_bytes|to_csv|to_parquet|to_json|json\.dump\(|"""
                          r"""shutil\.|\.save\(|os\.(makedirs|mkdir|rename|replace|remove)|\.mkdir\(|\.unlink\(|\.touch\(""")


def script_writes_other_batch_dir(cmd: str) -> str | None:
    """A python -c / heredoc body that names another session's live batch dir and writes something."""
    if not SCRIPT_WRITE.search(cmd):
        return None
    for wd, b, o in _live_batch_dirs():
        if re.search(re.escape(wd) + r"(?=[/'\"\s]|$)", cmd):
            return f"{wd} is the work dir of {b} (session {o[:8]})"
    return None


BATCH_DIR_HINT = ("write only into your own batch's work dir: reins batch open ... --work-dir .../{batch} (reins names and "
                  "makes the folder; never guess it before opening); reading another batch's dir is fine")


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
        from reins.sessions import git_verb
        rest, where = a[1:], cwd
        if "-C" in rest:
            i = rest.index("-C")
            where = (cwd / rest[i + 1]).resolve() if i + 1 < len(rest) else cwd
        verb = git_verb(rest)
        if (verb == "push" and "--dry-run" not in rest) or verb == "merge":
            return str(where)                        # merging is done by reins dev finish in a managed repo
    return None


def projects() -> list[tuple[Path, dict]]:
    """(repo, reins.toml) of every managed project under the code root (reins/projects.py)."""
    from reins.projects import all_
    return all_()


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
    """A wrapper script that executes a pipeline program (a resume script that calls the project's run script)."""
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
    if _switches_branch(cmd):
        o = other_sessions_worktree(cwd)
        if o or frozen(cwd + "/"):
            return f"no branch switching inside {o or cwd}: it is a release/batch tree or another session's worktree"
    ob = script_writes_other_batch_dir(cmd)
    if ob:
        return f"this script writes and names {ob}. {BATCH_DIR_HINT}"
    names = _assignments(cmd)
    for raw in write_targets(cmd):
        target = _expand(raw, names)
        if target is None:
            continue                                   # an unexpanded $VAR: the hook cannot know the path (fail open)
        try:
            target = os.path.abspath(os.path.join(cwd, target))
            why = frozen_reason(target)
            if why:
                return f"writing {target}: {why}"
            ob = other_sessions_batch_dir(target)
            if ob:
                return f"writing {target}: {ob}. {BATCH_DIR_HINT}"
            o = other_sessions_worktree(target)
        except (OSError, ValueError):
            continue
        if o:
            return (f"{target} is inside {o}. Start your own (reins dev start MODULE SUFFIX --about ...), or, if this "
                    f"session continues that one (a fork), take it over: reins session inherit")
    return None


ASSIGN = re.compile(r"""(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|[^\s;&|)]+)""")


def _assignments(cmd: str) -> dict[str, str]:
    """NAME=value assignments in the command itself (W=/data/x; ... > $W/out), outside here-doc bodies."""
    from reins.sessions import strip_heredocs
    out = {}
    for m in ASSIGN.finditer(strip_heredocs(cmd)):
        out[m.group(1)] = m.group(2).strip("'\"")
    return out


def _expand(target: str, names: dict[str, str]) -> str | None:
    """$NAME / ${NAME} from the command's own assignments, then the environment; None if a variable stays unknown."""
    def sub(m):
        k = m.group(1) or m.group(2)
        return names.get(k, os.environ.get(k, m.group(0)))
    for _ in range(3):
        target = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, target)
    return None if "$" in target or "`" in target else target


def _switches_branch(cmd: str) -> bool:
    """`git checkout BRANCH` / `git switch` among the programs executed (not a word in a message or a file path)."""
    from reins.sessions import git_verb, segments
    for seg in segments(cmd):
        a = seg["argv"]
        if Path(a[0]).name == "git" and git_verb(a[1:]) in ("checkout", "switch") and "--" not in a:
            return True
    return False


def write_targets(cmd: str) -> list[str]:
    """Paths a command writes: redirection targets and the file arguments of the writing programs (by argv)."""
    from reins.sessions import redirect_targets, segments
    out = redirect_targets(cmd)                                   # by tokens: quotes and here-doc bodies are text
    for seg in segments(cmd):
        a = seg["argv"]
        prog = Path(a[0]).name
        if prog not in WRITE_PROGRAMS:
            continue
        args = [x for x in a[1:] if not x.startswith("-")]
        if prog in MODE_FIRST:
            args = args[1:]
        if prog == "sed" and "-i" not in a[1:]:
            continue                                            # sed without -i only prints
        if prog == "sed":
            args = args[1:] if args and not Path(args[0]).exists() else args   # drop the script argument
        out += args
    return out


def check_edit(path: str) -> str | None:
    p = os.path.abspath(path)
    why = frozen_reason(p)
    if why:
        return f"editing {p}: {why}"
    ob = other_sessions_batch_dir(p)
    if ob:
        return f"editing {p}: {ob}. {BATCH_DIR_HINT}"
    o = other_sessions_worktree(p)
    if o:
        return (f"{p} is inside {o}. Start your own (reins dev start MODULE SUFFIX --about ...), or, if this session "
                f"continues that one (a fork), take it over: reins session inherit")
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
