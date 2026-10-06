"""Tell the user without being asked. Every notification is recorded; the same key is not repeated inside `cooldown`.

Delivery: a line in $REINS_HOME/notify.jsonl (the board reads it), plus `notify_cmd` from config.toml when set,
e.g.  notify_cmd = "notify-send '{title}' '{body}'".
"""
from __future__ import annotations

import datetime as dt
import json
import shlex
import subprocess

from .store import config, home, now, tx


def send(con, key: str, level: str, title: str, body: str = "", cooldown_min: int = 60) -> bool:
    """Returns False when the same key was sent within the cooldown (nothing sent)."""
    assert level in ("info", "warn", "action"), level
    with tx(con):
        last = con.execute("SELECT at FROM notification WHERE key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if last and cooldown_min and dt.datetime.fromisoformat(last[0]) > \
                dt.datetime.now() - dt.timedelta(minutes=cooldown_min):
            return False
        con.execute("INSERT INTO notification (at, key, level, title, body) VALUES (?,?,?,?,?)",
                    (now(), key, level, title, body))
    rec = {"at": now(), "level": level, "title": title, "body": body, "key": key}
    with (home() / "notify.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    cmd = config().get("notify_cmd")
    if cmd:
        try:
            subprocess.run(cmd.format(title=shlex.quote(title), body=shlex.quote(body), level=level),
                           shell=True, timeout=10, check=False)
        except Exception:                                                       # noqa: BLE001
            pass
    return True


def pending(con, limit: int = 20) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM notification WHERE acked=0 ORDER BY id DESC LIMIT ?", (limit,))]


def ack(con, ids: list[int]) -> None:
    with tx(con):
        con.executemany("UPDATE notification SET acked=1 WHERE id=?", [(i,) for i in ids])
