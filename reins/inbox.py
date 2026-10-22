"""Messages from one session to another, delivered by reins (C15/C16 hand-over).

reins knows who owns what (a batch's owner session, an issue's opener), so a message is addressed by role rather than
by guessing which open window is which. The Claude Code hook delivers unread messages to the addressee at its next
prompt (and at start), then marks them read.

    reins tell --batch B --title '...' 'body'        to the session that owns batch B
    reins tell --to SESSION --title '...' 'body'     to one session
    reins inbox [--all]                              this session's messages
"""
from __future__ import annotations

from .store import ReinsError, now, session, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_message (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  at            TEXT NOT NULL,
  to_session    TEXT NOT NULL,
  from_session  TEXT,
  batch_id      TEXT,
  title         TEXT NOT NULL,
  body          TEXT,
  read_at       TEXT
);
CREATE INDEX IF NOT EXISTS session_message_to ON session_message (to_session, read_at);
"""


def ensure(con) -> None:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_message'").fetchone():
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                con.execute(stmt)


def send(con, to: str, title: str, body: str = "", batch: str | None = None) -> int:
    ensure(con)
    if not to:
        raise ReinsError("no addressee")
    row = con.execute("SELECT id FROM session WHERE id LIKE ?", (to + "%",)).fetchone()
    to = row["id"] if row else to
    with tx(con):
        cur = con.execute("INSERT INTO session_message (at, to_session, from_session, batch_id, title, body)"
                          " VALUES (?,?,?,?,?,?)", (now(), to, session(), batch, title.strip(), body.strip()))
        return cur.lastrowid


def owner_of(con, batch: str) -> str | None:
    row = con.execute("SELECT holder FROM lease WHERE resource=?", (f"batch:{batch}",)).fetchone()
    if row:
        return row["holder"]
    row = con.execute("SELECT owner_session FROM batch WHERE batch_id=?", (batch,)).fetchone()
    return row["owner_session"] if row else None


def to_batch_owner(con, batch: str, title: str, body: str = "") -> int | None:
    """To whoever owns the batch now (after any hand-over). None when nobody does."""
    o = owner_of(con, batch)
    # the batch's own supervisor runs under its owner's session id: what it reports (a sentinel pause, an open stage)
    # is for that session, not "to myself" (2026-10-22: every such message had been dropped)
    import os
    if not o or (o == session() and not os.environ.get("REINS_SUPERVISOR")):
        return None
    return send(con, o, title, body, batch)


def unread(con, sid: str, mark: bool = True) -> list[dict]:
    ensure(con)
    rows = [dict(r) for r in con.execute("SELECT * FROM session_message WHERE to_session=? AND read_at IS NULL ORDER BY id",
                                         (sid,))]
    if rows and mark:
        with tx(con):
            con.executemany("UPDATE session_message SET read_at=? WHERE id=?", [(now(), r["id"]) for r in rows])
    return rows


def render(rows: list[dict]) -> str:
    out = []
    for r in rows:
        src = f" from session {r['from_session'][:8]}" if r["from_session"] else ""
        out.append(f"[reins inbox] {r['at'][5:16]}{src}{' about ' + r['batch_id'] if r['batch_id'] else ''}: "
                   f"{r['title']}" + (f"\n  {r['body']}" if r["body"] else ""))
    return "\n".join(out)
