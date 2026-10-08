#!/usr/bin/env python3
"""Claude Code hook for every session: records what each session does into reins (C15) and guards (C7/C10).

One script, all events (install in ~/.claude/settings.json, see docs/WORKFLOW.md):
  SessionStart       register the session, detect a fork, tell it who it is and what it owns (additionalContext)
  PreToolUse Bash    classify the command (run_start / run_stop / experiment / merge / git / ...), record it;
                     block what must go through reins (guard.py rules), recording the block
  PostToolUse Edit|Write|MultiEdit|NotebookEdit   record the edited file -> repo, worktree, module
  Stop               last_seen; SessionEnd: mark ended

Never fails the session: any internal error allows the action and is logged to $REINS_HOME/hook.log.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))


def log(msg: str) -> None:
    try:
        from reins.store import DEFAULT_HOME
        home = Path(os.environ.get("REINS_HOME", DEFAULT_HOME))
        with (home / "hook.log").open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception as e:                                                      # noqa: BLE001
        log(f"bad input: {e!r}")
        return 0
    ev = data.get("hook_event_name", "")
    sid = data.get("session_id") or ""
    cwd = data.get("cwd")
    if not sid:
        return 0
    os.environ["REINS_SESSION"] = sid                           # the session id of the hook's caller, not ours
    try:
        from reins import sessions
        from reins.store import connect
        con = connect()
        if ev == "SessionStart":
            text = sessions.start(con, sid, cwd, data.get("transcript_path"), data.get("source", "startup"))
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
            return 0
        if ev == "SessionEnd":
            sessions.end(con, sid, data.get("reason", ""))
            return 0
        if ev in ("Stop", "SubagentStop", "UserPromptSubmit"):
            from reins.store import now, tx
            with tx(con):
                con.execute("UPDATE session SET last_seen=?, status='active' WHERE id=?", (now(), sid))
            return 0
        tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
        if ev == "PreToolUse" and tool == "Bash":
            cmd = inp.get("command", "")
            from reins.store import DEFAULT_HOME
            if (Path(os.environ.get("REINS_HOME", DEFAULT_HOME)) / "hook.debug").exists():
                log(f"debug Bash cwd(json)={cwd} getcwd={os.getcwd()} cmd={cmd[:120]!r}")
            import guard
            msg = guard.check_bash(cmd)
            kind = sessions.classify(cmd)
            if msg:
                sessions.event(con, sid, "blocked", cwd=cwd, detail=f"{msg} :: {cmd}")
                print(f"reins guard: {msg}", file=sys.stderr)
                return 2
            if kind != "read":
                sessions.event(con, sid, kind, cwd=cwd, detail=cmd)
            return 0
        if ev == "PreToolUse" and tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            import guard
            path = inp.get("file_path") or inp.get("notebook_path") or ""
            msg = guard.check_edit(path)
            if msg:
                sessions.event(con, sid, "blocked", cwd=cwd, path=path, detail=msg)
                print(f"reins guard: {msg}", file=sys.stderr)
                return 2
            return 0
        if ev == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            path = inp.get("file_path") or inp.get("notebook_path") or ""
            if path:
                sessions.event(con, sid, "edit", cwd=cwd, path=os.path.abspath(path))
            return 0
        return 0
    except Exception as e:                                                      # noqa: BLE001
        log(f"{ev} {sid[:8]}: {e!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
