"""C10: one holder per resource. A session that does not hold the lease is refused, not merely warned."""
from __future__ import annotations

from .store import ReinsError, now, session, tx


def acquire(con, resource: str, purpose: str = "", holder: str | None = None) -> None:
    holder = holder or session()
    if not holder:
        raise ReinsError("no session id (CLAUDE_CODE_SESSION_ID or REINS_SESSION): a lease needs a holder")
    with tx(con):
        row = con.execute("SELECT holder, since, purpose FROM lease WHERE resource=?", (resource,)).fetchone()
        if row and row["holder"] != holder:
            raise ReinsError(f"{resource} is held by session {row['holder']} since {row['since']} ({row['purpose']}). "
                             f"Ask that session to release it: reins lease release {resource!r}")
        if not row:
            con.execute("INSERT INTO lease VALUES (?,?,?,?)", (resource, holder, purpose, now()))


def release(con, resource: str, holder: str | None = None, force: bool = False) -> None:
    holder = holder or session()
    with tx(con):
        row = con.execute("SELECT holder FROM lease WHERE resource=?", (resource,)).fetchone()
        if not row:
            return
        if row["holder"] != holder and not force:
            raise ReinsError(f"{resource} is held by {row['holder']}, not you; --force to take it anyway (recorded)")
        con.execute("DELETE FROM lease WHERE resource=?", (resource,))


def check(con, resource: str, holder: str | None = None) -> None:
    """Raise unless `holder` (default: this session) holds `resource` or nobody does."""
    holder = holder or session()
    row = con.execute("SELECT holder, since FROM lease WHERE resource=?", (resource,)).fetchone()
    if row and row["holder"] != holder:
        raise ReinsError(f"{resource} belongs to session {row['holder']} (since {row['since']}); you are "
                         f"{holder or 'an unknown session'}. If you were forked from it, start your own work: "
                         f"reins dev start ...")


def holder_of(con, resource: str) -> str | None:
    row = con.execute("SELECT holder FROM lease WHERE resource=?", (resource,)).fetchone()
    return row["holder"] if row else None


def list_(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM lease ORDER BY since")]
