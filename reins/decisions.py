"""C13 decisions: dated, with evidence, supersedable. A new session reads the active ones instead of a memory."""
from __future__ import annotations

from .store import ReinsError, now, session, tx


def add(con, text: str, evidence: str, by: str, project: str | None = None, supersedes: int | None = None) -> int:
    if not text.strip() or not evidence.strip():
        raise ReinsError("a decision needs text and evidence (batch id, benchmark, n, or 'user ruling')")
    with tx(con):
        cur = con.execute("INSERT INTO decision (at, project, text, evidence, by_whom, status, session) VALUES (?,?,?,?,?,'active',?)",
                          (now(), project, text, evidence, by, session()))
        new_id = cur.lastrowid
        if supersedes:
            old = con.execute("SELECT status FROM decision WHERE id=?", (supersedes,)).fetchone()
            if not old:
                raise ReinsError(f"decision #{supersedes} does not exist")
            con.execute("UPDATE decision SET status='superseded', superseded_by=? WHERE id=?", (new_id, supersedes))
    return new_id


def list_(con, project: str | None = None, all_: bool = False) -> list[dict]:
    q, args = "SELECT * FROM decision", []
    conds = []
    if not all_:
        conds.append("status='active'")
    if project:
        conds.append("(project=? OR project IS NULL)"); args.append(project)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return [dict(r) for r in con.execute(q + " ORDER BY id", args)]
