"""Hints: a known failure seen in a command's output, or a known wrong move in a reply, gets the fix at once.

2026-11-05 a session's S3 listing failed on an expired login and it went on with an index from 2026-08-21, saying so
in passing; the user: "你重复犯这个错误，我希望永远不再发生". Memory and docs had the right way; the session did not
look. Now the machine's config.toml carries the rule and the hook applies it every time:

    [[hints]]
    name = "expired-login"
    output = "session has expired|ExpiredToken|Unable to locate credentials"   # regex on a command's output
    reply = "登录已过期.{0,60}(旧|索引|缓存)|index from|cached (index|listing)" # regex on the assistant's reply
    message = "what to do instead"

output: after a Bash command whose output matches, the message is added to the session's context (once per name per
session per hour). reply: a reply that matches is sent back once (Stop hook block) with the message, so the session
fixes the work rather than reporting the workaround. Words and paths live in config.toml, never here.
"""
from __future__ import annotations

import re
import time

from .store import config, home


def rules() -> list[dict]:
    out = []
    for h in config().get("hints") or []:
        try:
            out.append({"name": h["name"], "message": h["message"],
                        "output": re.compile(h["output"], re.I) if h.get("output") else None,
                        "reply": re.compile(h["reply"], re.I | re.S) if h.get("reply") else None})
        except (KeyError, re.error):
            continue
    return out


def _once(sid: str, name: str, every_s: int = 3600) -> bool:
    f = home() / "hints_seen"
    key = f"{sid}:{name}"
    seen = {}
    try:
        for line in f.read_text().splitlines():
            k, t = line.rsplit(" ", 1)
            seen[k] = float(t)
    except (OSError, ValueError):
        pass
    if time.time() - seen.get(key, 0) < every_s:
        return False
    seen[key] = time.time()
    keep = {k: t for k, t in seen.items() if time.time() - t < 86400}
    try:
        f.write_text("".join(f"{k} {t}\n" for k, t in keep.items()))
    except OSError:
        pass
    return True


def for_output(sid: str, text: str) -> list[str]:
    return [f"[reins hint: {h['name']}] {h['message']}" for h in rules()
            if h["output"] and text and h["output"].search(text) and _once(sid, h["name"])]


QUOTED = re.compile(r"```.*?```|`[^`\n]*`|“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"\n]*\"", re.S)


def own_words(text: str) -> str:
    """The reply without what it quotes: a quoted sentence (the bad one being explained) is not the session's claim."""
    return QUOTED.sub(" ", text)


def for_reply(texts: list[str]) -> list[str]:
    body = own_words("\n".join(texts))
    return [f"[reins hint: {h['name']}] {h['message']}" for h in rules() if h["reply"] and h["reply"].search(body)]


def _text(resp) -> str:
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output", "error"))
    return str(resp or "")
