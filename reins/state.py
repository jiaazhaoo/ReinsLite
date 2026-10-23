"""The only writer of batch and stage status.

2026-10-23 the state "never seemed right": batch and stage status were set by 19 UPDATE statements in 6 modules, each
with its own rule, and only the normal stage end checked that every case had an outcome -- 60 of 148 done batches had
done stages with cases unaccounted for. Now every change goes through here:

  - the transition must be in the table below (a done batch does not start running again; a skipped stage stays
    skipped);
  - the guards hold at the moment of the change: a stage becomes done only when every case of the batch has an outcome
    there, or the batch records no per-case outcomes (per_case = 0); a batch becomes done only when every stage is
    done or skipped;
  - every change is one 'state' event on the batch (from -> to, why), so the history says how the state came about.

tests/test_state.py refuses any other module that writes a status column of batch or batch_stage. Callers run inside
their own transaction (store.tx); nothing here opens one.
"""
from __future__ import annotations

from .store import ReinsError, now

KEEP = object()                                   # batch_to(reason=KEEP): leave status_reason as it is

BATCH = {"open": {"running", "paused", "failed", "done", "closed"},
         "running": {"paused", "done", "failed", "closed"},
         "paused": {"running", "done", "failed", "closed"},
         "done": {"closed"},
         "failed": {"closed"},
         "closed": set()}
STAGE = {"planned": {"running", "skipped"},
         "running": {"done", "failed", "skipped"},
         "failed": {"running", "done", "skipped"},          # restarted; or its failing checks acknowledged
         "done": set(),
         "skipped": set()}


def _event(con, bid: str, detail: str) -> None:
    from .store import session
    con.execute("INSERT INTO batch_event (at, batch_id, event, detail, session) VALUES (?,?, 'state', ?, ?)",
                (now(), bid, detail[:1000], session()))


def missing(con, bid: str, stage: str) -> list[str]:
    """Cases of the batch without an outcome in this stage."""
    return [r[0] for r in con.execute(
        "SELECT c.case_id FROM batch_case c LEFT JOIN case_current e ON e.batch_id=c.batch_id AND e.stage=?"
        " AND e.case_id=c.case_id WHERE c.batch_id=? AND (e.status IS NULL OR e.status='started') ORDER BY c.case_id",
        (stage, bid))]


def per_case(con, bid: str) -> bool:
    r = con.execute("SELECT COALESCE(per_case, 1) FROM batch WHERE batch_id=?", (bid,)).fetchone()
    return bool(r and r[0])


def no_per_case(con, bid: str, why: str) -> None:
    """The batch records no per-case outcomes (an experiment that reports nothing per case): its stages may end without
    them, and its counts are shown as 'not recorded per case'."""
    if per_case(con, bid):
        con.execute("UPDATE batch SET per_case=0 WHERE batch_id=?", (bid,))
        _event(con, bid, f"per_case 1 -> 0 ({why})")


def account(con, bid: str, stage: str, with_result: set[str], reason: str) -> tuple[int, int]:
    """Give every case without an outcome in this stage one: done when the run's results file has it (it reached the
    end), else skipped with the reason. Returns (done, skipped)."""
    gone = missing(con, bid, stage)
    if not gone:
        return 0, 0
    t = now()
    ok = [c for c in gone if c in with_result]
    rest = [c for c in gone if c not in with_result]
    con.executemany("INSERT INTO case_event (at, batch_id, stage, case_id, status, reason) VALUES (?,?,?,?,?,?)",
                    [(t, bid, stage, c, "done", "has a result in the run's results file") for c in ok]
                    + [(t, bid, stage, c, "skipped", reason) for c in rest])
    _event(con, bid, f"{stage}: {len(ok)} cases done from the results file, {len(rest)} skipped ({reason})")
    return len(ok), len(rest)


def batch_to(con, bid: str, to: str, why: str = "", *, reason=KEEP, closed: bool = False) -> str:
    """Move a batch to `to`; returns the status it had. `reason`: KEEP leaves status_reason, None clears it, a string
    sets it. `closed` stamps the closed time."""
    row = con.execute("SELECT status FROM batch WHERE batch_id=?", (bid,)).fetchone()
    if not row:
        raise ReinsError(f"batch {bid!r} is not registered")
    old = row[0]
    if to == old:
        return old
    if to not in BATCH[old]:
        raise ReinsError(f"batch {bid} is {old}; it cannot become {to}")
    if to == "done":
        left = [r[0] for r in con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status NOT IN ('done','skipped')", (bid,))]
        if left:
            raise ReinsError(f"batch {bid} cannot be done: stage(s) not finished: {', '.join(left)}")
    if reason is KEEP:
        con.execute("UPDATE batch SET status=?, closed=CASE WHEN ? THEN COALESCE(closed, ?) ELSE closed END WHERE batch_id=?",
                    (to, int(closed), now(), bid))
    else:
        con.execute("UPDATE batch SET status=?, status_reason=?, closed=CASE WHEN ? THEN COALESCE(closed, ?) ELSE closed END"
                    " WHERE batch_id=?", (to, reason, int(closed), now(), bid))
    _event(con, bid, f"batch {old} -> {to}" + (f" ({why})" if why else ""))
    return old


def stage_to(con, bid: str, stage: str, to: str, note: str | None = None, *, module_version: str | None = None,
             output_path: str | None = None, log_path: str | None = None) -> str:
    """Move a stage to `to`; returns the status it had. A stage becomes done only when every case has an outcome there
    (or the batch records none per case)."""
    row = con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage=?", (bid, stage)).fetchone()
    if not row:
        raise ReinsError(f"stage {stage!r} is not planned in {bid}")
    old = row[0]
    if to == old:
        return old
    if to not in STAGE[old]:
        raise ReinsError(f"stage {stage} of {bid} is {old}; it cannot become {to}")
    if to == "done" and per_case(con, bid):
        gone = missing(con, bid, stage)
        if gone:
            raise ReinsError(f"stage {stage} cannot be done: {len(gone)} cases have no outcome (first: {', '.join(gone[:10])}). "
                             f"Mark them done/skipped/failed")
    t = now()
    if to == "running":
        con.execute("UPDATE batch_stage SET status='running', module_version=COALESCE(?, module_version),"
                    " started=COALESCE(started, ?), output_path=COALESCE(?, output_path), log_path=COALESCE(?, log_path),"
                    " note=COALESCE(?, note) WHERE batch_id=? AND stage=?",
                    (module_version, t, output_path, log_path, note, bid, stage))
    else:
        con.execute("UPDATE batch_stage SET status=?, ended=?, note=COALESCE(?, note) WHERE batch_id=? AND stage=?",
                    (to, t, note[:1000] if note else None, bid, stage))
    _event(con, bid, f"{stage} {old} -> {to}" + (f" ({note[:300]})" if note else ""))
    return old


def maybe_done(con, bid: str, why: str = "all stages finished") -> bool:
    """The batch becomes done when no stage is left and it is still live."""
    left = con.execute("SELECT COUNT(*) FROM batch_stage WHERE batch_id=? AND status NOT IN ('done','skipped')", (bid,)).fetchone()[0]
    st = con.execute("SELECT status FROM batch WHERE batch_id=?", (bid,)).fetchone()[0]
    if left == 0 and st in ("open", "running", "paused"):
        batch_to(con, bid, "done", why)
        return True
    return False
