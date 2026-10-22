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


def _live(con, sid: str) -> list[str]:
    """What other sessions changed meanwhile about what this one is working on (the live feed, C17)."""
    try:
        from reins import knowledge
        text = knowledge.render_changes(knowledge.pending(con, sid))
        return [text] if text else []
    except Exception as e:                                                      # noqa: BLE001
        log(f"live: {e!r}")
        return []


def _ask_for_facts(con, sid: str) -> str:
    try:
        from reins import knowledge
        from reins.store import now, tx
        subj = knowledge.unrecorded(con, sid)
        if not subj:
            return ""
        with tx(con):
            for x in subj:
                con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?,?, 'asked_note', ?)", (now(), sid, x))
        return ("[reins] Before you stop: you changed " + ", ".join(subj) + " and left nothing for the next session. "
                "Write what will still be true tomorrow and someone would otherwise have to rediscover (how it is built, "
                "what it can and cannot do, what you changed on a live service), one line each: "
                "reins note add SUBJECT '...'  or, for a change to a live service, reins service event NAME '...'. "
                "If there is nothing lasting, say so in one line and stop. (Asked once per subject.)")
    except Exception as e:                                                      # noqa: BLE001
        log(f"ask: {e!r}")
        return ""


def _ask_for_batches(con, sid: str) -> str:
    """Batches this session owns with nothing alive: asked once which end each gets (2026-10-22: 68 batches were left
    open, every owner had moved on after reporting results)."""
    try:
        from reins import lifecycle
        from reins.store import now, tx
        rows = lifecycle.loose_for(con, sid)
        asked = {r[0] for r in con.execute("SELECT detail FROM session_event WHERE session_id=? AND kind='asked_batch'", (sid,))}
        rows = [r for r in rows if r["batch_id"] not in asked]
        if not rows:
            return ""
        with tx(con):
            for r in rows:
                con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?,?, 'asked_batch', ?)",
                            (now(), sid, r["batch_id"]))
        lines = "\n".join(f"  {r['batch_id']} ({r['type']}, {r['status']}: {(r['status_reason'] or '-')[:70]})" for r in rows[:12])
        return ("[reins] Before you stop: you own batches with nothing running. Give each an end so it does not stay open "
                "for ever:\n" + lines + "\n  finished -> reins batch stage-end B STAGE (or it ends by itself if it reported "
                "no cases);  will not be finished -> reins batch abandon B --why '...';  still yours, to resume later -> say so "
                "in one line. (Asked once per batch.)")
    except Exception as e:                                                      # noqa: BLE001
        log(f"ask batches: {e!r}")
        return ""


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
            if ev == "Stop" and not data.get("stop_hook_active"):
                ask = _ask_for_facts(con, sid) or _ask_for_batches(con, sid)
                if ask:                                  # changed a module or a service and left no fact: ask once
                    print(json.dumps({"decision": "block", "reason": ask}))
                    return 0
            if ev == "UserPromptSubmit":                      # deliver what other sessions sent to this one
                from reins import inbox
                prompt = data.get("prompt") or ""
                _purpose_from_prompt(con, sid, prompt)
                parts = []
                msgs = inbox.unread(con, sid)
                if msgs:
                    parts.append(inbox.render(msgs))
                parts += _briefs(con, sid, _subjects_in(con, prompt))
                parts += _live(con, sid)
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
        if ev == "PostToolUse" and tool == "Bash":
            parts = []
            try:
                from reins import knowledge
                hit = knowledge.capture_command(con, sid, inp.get("command", ""), cwd)   # recorded as it ran
                parts += _briefs(con, sid, hit)
            except Exception as e:                                              # noqa: BLE001
                log(f"capture: {e!r}")
            parts += _live(con, sid)
            if parts:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n\n".join(parts)}}))
            return 0
        if ev == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            path = inp.get("file_path") or inp.get("notebook_path") or ""
            parts = []
            if path:
                sessions.event(con, sid, "edit", cwd=cwd, path=os.path.abspath(path))
                row = con.execute("SELECT module FROM session_event WHERE session_id=? AND kind='edit' ORDER BY id DESC LIMIT 1",
                                  (sid,)).fetchone()
                try:
                    from reins import knowledge
                    hit = knowledge.capture_edit(con, sid, path)
                except Exception as e:                                          # noqa: BLE001
                    log(f"capture: {e!r}")
                    hit = []
                parts = _briefs(con, sid, ([row[0]] if row and row[0] else []) + hit)   # the first edit: what is known
            parts += _live(con, sid)
            if parts:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n\n".join(parts)}}))
            return 0
        return 0
    except Exception as e:                                                      # noqa: BLE001
        log(f"{ev} {sid[:8]}: {e!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
