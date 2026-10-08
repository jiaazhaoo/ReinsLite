"""C16 issues: the handoff object between a run and a fix.

A run (or the review platform) finds wrong cases -> an issue on that batch -> a development session takes it
(reins dev start --issue N: the cases become the pilot set) -> the version is released -> the issue is 'fixed'
and the session that opened it is told which version to resume with -> that session verifies on the issue's cases
-> closed. Every step is on the issue; the board shows open issues on the run card and the cause on the dev card.

    reins issue open --batch B --cases ID,ID --symptom '...' [--stage S]
    reins issue from-review B [--stage check]           one issue per error_type from reviewers' 'wrong' verdicts
    reins issue list [--all]
    reins issue show N
    reins issue note N '...'
    reins issue verify N [--by USER] [--note '...']     the opener confirms the fix on the issue's cases
    reins issue close N --why '...'                      or: not a defect / duplicate / won't fix
"""
from __future__ import annotations

import json

from . import batches, notify
from .store import ReinsError, now, session, tx

STATUSES = ("open", "in_progress", "fixed", "verified", "closed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS issue (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  at            TEXT NOT NULL,
  batch_id      TEXT NOT NULL,
  stage         TEXT,
  cases         TEXT NOT NULL,                        -- JSON list of case_id
  symptom       TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('open','in_progress','fixed','verified','closed')),
  from_session  TEXT,                                 -- who found it (gets told when it is fixed)
  version       TEXT,                                 -- the module version fixing it
  fix_session   TEXT,
  error_type    TEXT,
  closed_at     TEXT,
  close_why     TEXT
);
CREATE TABLE IF NOT EXISTS issue_event (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  issue_id  INTEGER NOT NULL REFERENCES issue(id),
  event     TEXT NOT NULL,
  detail    TEXT,
  session   TEXT
);
"""


def ensure(con) -> None:
    """Create the tables once. Never an executescript inside an open transaction (it would commit it)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issue'").fetchone():
        con.executescript(SCHEMA)


def _ev(con, iid: int, event: str, detail: str = "") -> None:
    con.execute("INSERT INTO issue_event (at, issue_id, event, detail, session) VALUES (?,?,?,?,?)",
                (now(), iid, event, detail, session()))


def get(con, iid: int):
    ensure(con)
    row = con.execute("SELECT * FROM issue WHERE id=?", (iid,)).fetchone()
    if not row:
        raise ReinsError(f"issue #{iid} does not exist")
    return row


def open_(con, batch: str, cases: list[str], symptom: str, stage: str | None = None, error_type: str | None = None,
          from_session: str | None = None) -> int:
    ensure(con)
    batches.get(con, batch)
    if not symptom.strip() or len(symptom.strip()) < 8:
        raise ReinsError("symptom: one line saying what is wrong, as seen on the cases")
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (batch,))}
    bad = [c for c in cases if c not in members]
    if bad:
        raise ReinsError(f"not in {batch}: {bad[:5]}")
    if not cases:
        raise ReinsError("an issue names at least one case: the fix is verified on them")
    with tx(con):
        cur = con.execute("INSERT INTO issue (at, batch_id, stage, cases, symptom, status, from_session, error_type)"
                          " VALUES (?,?,?,?,?, 'open', ?,?)",
                          (now(), batch, stage, json.dumps(sorted(set(cases))), symptom.strip(), from_session or session(), error_type))
        iid = cur.lastrowid
        _ev(con, iid, "opened", f"{len(cases)} cases: {symptom.strip()}")
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), batch, "issue_opened", f"#{iid}: {symptom.strip()[:120]} ({len(cases)} cases)", session()))
    return iid


def from_review(con, batch: str, stage: str = "check") -> list[int]:
    """One issue per error_type, from reviewers' current 'wrong' verdicts on the batch that no open issue covers yet."""
    ensure(con)
    from . import review
    covered = set()
    for r in con.execute("SELECT cases FROM issue WHERE batch_id=? AND status<>'closed'", (batch,)):
        covered |= set(json.loads(r[0]))
    groups: dict[str, set] = {}
    for (case, _t, rev), v in review.current_verdicts(con, batch).items():
        if v["verdict"] == "wrong" and case not in covered:
            groups.setdefault(v["error_type"] or "unspecified", set()).add(case)
    out = []
    for et, cases in sorted(groups.items()):
        out.append(open_(con, batch, sorted(cases), f"reviewers marked {len(cases)} cases wrong: {et}", stage, et))
    return out


def take(con, iid: int, version: str) -> list[str]:
    """A development session takes the issue with a version; returns the pilot cases."""
    with tx(con):
        row = get(con, iid)
        if row["status"] not in ("open", "in_progress"):
            raise ReinsError(f"issue #{iid} is {row['status']}")
        con.execute("UPDATE issue SET status='in_progress', version=?, fix_session=? WHERE id=?", (version, session(), iid))
        _ev(con, iid, "taken", f"version {version}")
    return json.loads(row["cases"])


def fixed(con, version: str) -> list[int]:
    """Called when a version is released: its issues become 'fixed' and the openers are told what to resume with."""
    ensure(con)
    out = []
    for row in con.execute("SELECT * FROM issue WHERE version=? AND status='in_progress'", (version,)).fetchall():
        with tx(con):
            con.execute("UPDATE issue SET status='fixed' WHERE id=?", (row["id"],))
            _ev(con, row["id"], "fixed", f"{version} released")
        b = batches.get(con, row["batch_id"])
        notify.send(con, f"issue_fixed:{row['id']}", "action",
                    f"issue #{row['id']} fixed by {version}: {row['symptom'][:80]}",
                    f"batch {row['batch_id']} (status {b['status']}) can resume stage {row['stage'] or '?'} with {version} "
                    f"(reins run ... , the batch becomes mixed_version); then reins issue verify {row['id']} on cases "
                    f"{', '.join(json.loads(row['cases'])[:5])}", cooldown_min=0)
        out.append(row["id"])
    return out


def verify(con, iid: int, by: str, note: str = "") -> None:
    with tx(con):
        row = get(con, iid)
        if row["status"] != "fixed":
            raise ReinsError(f"issue #{iid} is {row['status']}; verify after its version is released")
        con.execute("UPDATE issue SET status='verified' WHERE id=?", (iid,))
        _ev(con, iid, "verified", f"by {by}: {note}")


def close(con, iid: int, why: str) -> None:
    if not why.strip():
        raise ReinsError("closing needs a reason")
    with tx(con):
        row = get(con, iid)
        if row["status"] == "closed":
            raise ReinsError("already closed")
        con.execute("UPDATE issue SET status='closed', closed_at=?, close_why=? WHERE id=?", (now(), why, iid))
        _ev(con, iid, "closed", why)


def note(con, iid: int, text: str) -> None:
    with tx(con):
        get(con, iid)
        _ev(con, iid, "note", text)


def list_(con, all_: bool = False, batch: str | None = None) -> list[dict]:
    ensure(con)
    q, args = "SELECT * FROM issue", []
    conds = [] if all_ else ["status NOT IN ('closed','verified')"]
    if batch:
        conds.append("batch_id=?"); args.append(batch)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    out = []
    for r in con.execute(q + " ORDER BY id DESC", args):
        d = dict(r); d["cases"] = json.loads(d["cases"]); out.append(d)
    return out


def show(con, iid: int) -> dict:
    row = dict(get(con, iid))
    row["cases"] = json.loads(row["cases"])
    row["events"] = [dict(e) for e in con.execute("SELECT at, event, detail FROM issue_event WHERE issue_id=? ORDER BY id", (iid,))]
    return row
