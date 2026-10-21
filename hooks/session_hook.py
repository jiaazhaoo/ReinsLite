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


BRIEF_CHARS = 3000          # one brief, at most; at most two per prompt


def _subjects_in(con, prompt: str) -> list[str]:
    try:
        from reins import knowledge
        return knowledge.mentioned(con, prompt)[:2]
    except Exception as e:                                                      # noqa: BLE001
        log(f"subjects: {e!r}")
        return []


def _briefs(con, sid: str, subjects: list[str]) -> list[str]:
    """The brief of each subject this session has not been handed yet (C17: shared knowledge reaches the session)."""
    out = []
    try:
        from reins import knowledge
        for subj in subjects:
            if subj and not knowledge.briefed(con, sid, subj):
                text = knowledge.brief(con, subj)
                out.append("[reins] What is known about " + subj + " (other sessions' work, live services, open problems, "
                           "facts). Read it before deciding what is possible or proposing a fix:\n"
                           + (text if len(text) <= BRIEF_CHARS else text[:BRIEF_CHARS] + "\n... (reins brief " + subj + ")"))
    except Exception as e:                                                      # noqa: BLE001  a hook never fails the prompt
        log(f"brief: {e!r}")
    return out


def _purpose_from_prompt(con, sid: str, prompt: str) -> None:
    """A session that never said what it is for is known by its first request, so the others can see it."""
    text = " ".join((prompt or "").split())
    if len(text) < 6 or text.startswith("<"):
        return
    try:
        from reins.store import tx
        with tx(con):
            con.execute("UPDATE session SET purpose=? WHERE id=? AND (purpose IS NULL OR purpose='')",
                        ("(first prompt) " + text[:90], sid))
    except Exception as e:                                                      # noqa: BLE001
        log(f"purpose: {e!r}")


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
            from reins import inbox
            msgs = inbox.unread(con, sid)
            if msgs:
                text += "\n" + inbox.render(msgs)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
            return 0
        if ev == "SessionEnd":
            sessions.end(con, sid, data.get("reason", ""))
            return 0
        if ev in ("Stop", "SubagentStop", "UserPromptSubmit"):
            from reins.store import now, tx
            with tx(con):
                con.execute("UPDATE session SET last_seen=?, status='active' WHERE id=?", (now(), sid))
            if ev == "UserPromptSubmit":                      # deliver what other sessions sent to this one
                from reins import inbox
                prompt = data.get("prompt") or ""
                _purpose_from_prompt(con, sid, prompt)
                parts = []
                msgs = inbox.unread(con, sid)
                if msgs:
                    parts.append(inbox.render(msgs))
                parts += _briefs(con, sid, _subjects_in(con, prompt))
                if parts:
                    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                             "additionalContext": "\n\n".join(parts)}}))
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
                row = con.execute("SELECT module FROM session_event WHERE session_id=? AND kind='edit' ORDER BY id DESC LIMIT 1",
                                  (sid,)).fetchone()
                parts = _briefs(con, sid, [row[0]] if row and row[0] else [])
                if parts:                                    # the first edit of a module: what is known about it
                    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                             "additionalContext": "\n\n".join(parts)}}))
            return 0
        return 0
    except Exception as e:                                                      # noqa: BLE001
        log(f"{ev} {sid[:8]}: {e!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
