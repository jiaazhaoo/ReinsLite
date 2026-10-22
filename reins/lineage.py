"""One piece of work, many runs. A work package is re-run (same cases), split (a part of the cases), finished (the
later steps again on the same cases), extended (more cases); each run is a batch. Regression for 2026-10-22: 32
production / rework batches were 7 pieces of work (Mansfield Low alone 11 batches); the superseded runs stayed open,
nothing said which run holds the answer, and `parent` was never filled.

    relate(con, batch)        at open: the newest earlier batch of the same project and scope that shares cases ->
                              parent, and the relation (rerun | part | extends | overlaps), recorded as an event
    backfill(con)             the same for batches opened before this existed
    settle(con, batch)        for a production / rework batch with nothing alive (reins/lifecycle.py sweep):
                                every case has its result      -> done ("results complete")
                                a later run covers its cases   -> closed, superseded by that run
                                idle stale_days, some results  -> done, partial (its results still count)
                                idle stale_days, no result     -> closed, stopped
The person starts runs; how a run ends is decided here and recorded (user 2026-10-22).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import batches
from .store import config, now, tx

RELATIONS = ("rerun", "part", "extends", "overlaps")


def _cases(con, bid: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (bid,))}


def relation(mine: set, theirs: set) -> str | None:
    if not mine & theirs:
        return None
    if mine == theirs:
        return "rerun"
    if mine < theirs:
        return "part"
    if mine > theirs:
        return "extends"
    return "overlaps"


def relate(con, bid: str) -> dict | None:
    b = batches.get(con, bid)
    mine = _cases(con, bid)
    best = None                          # the strongest relation (rerun > part > extends > overlaps), newest on a tie
    for o in con.execute("SELECT batch_id FROM batch WHERE project=? AND scope=? AND created<? AND batch_id<>?"
                         " ORDER BY created DESC", (b["project"], b["scope"], b["created"], bid)).fetchall():
        rel = relation(mine, _cases(con, o[0]))
        if rel and (best is None or RELATIONS.index(rel) < RELATIONS.index(best[1])):
            best = (o, rel)
    if best:
        o, rel = best
        if True:
            with tx(con):
                con.execute("UPDATE batch SET parent=? WHERE batch_id=? AND parent IS NULL", (o[0], bid))
                if not con.execute("SELECT 1 FROM batch_event WHERE batch_id=? AND event='lineage'", (bid,)).fetchone():
                    batches._event(con, bid, "lineage", f"{rel} of {o[0]}")
            return {"parent": o[0], "relation": rel}
    return None


def backfill(con) -> int:
    n = 0
    for (bid,) in con.execute("SELECT batch_id FROM batch WHERE parent IS NULL ORDER BY created").fetchall():
        if relate(con, bid):
            n += 1
    return n


def results_of(con, b) -> set[str]:
    """The cases of a batch that have a result in its project's results file ([results] in reins.toml)."""
    from . import production
    from .paths import work_dir
    spec = production._cfg(b["project"]).get("results") or {}
    wd = work_dir(b)
    if not spec.get("file") or not wd:
        return set()
    return set(production._results(Path(wd) / spec["file"], spec)) & _cases(con, b["batch_id"])


def superseded_by(con, b) -> str | None:
    """A later run of the same scope whose cases (with the runs after it) cover this batch's cases."""
    mine = _cases(con, b["batch_id"])
    # only runs of the same kind supersede: an experiment on production cases does not replace the production run
    kinds = list(batches.STRICT_TYPES) if b["type"] in batches.STRICT_TYPES else [b["type"]]
    later = con.execute(f"SELECT batch_id FROM batch WHERE project=? AND scope=? AND created>? AND status NOT IN ('failed')"
                        f" AND type IN ({','.join('?' * len(kinds))}) ORDER BY created",
                        (b["project"], b["scope"], b["created"], *kinds)).fetchall()
    covered, first = set(), None
    for (o,) in later:
        oc = _cases(con, o) & mine
        if oc:
            covered |= oc
            first = first or o
        if covered >= mine and first:
            return first
    return None


def _finish(con, bid: str, note: str) -> None:
    t = now()
    with tx(con):
        for st in con.execute("SELECT stage, status FROM batch_stage WHERE batch_id=? AND status NOT IN ('done','skipped')",
                              (bid,)).fetchall():
            new = "skipped" if st["status"] == "planned" else "done"
            con.execute("UPDATE batch_stage SET status=?, ended=COALESCE(ended, ?), note=? WHERE batch_id=? AND stage=?",
                        (new, t, note[:500], bid, st["stage"]))
        con.execute("UPDATE batch SET status='done', status_reason=? WHERE batch_id=?", (note[:300], bid))
        batches._event(con, bid, "finished", note)
        batches._event(con, bid, "done", "settled by reins (lineage)")


def _close(con, bid: str, why: str) -> None:
    with tx(con):
        con.execute("UPDATE batch SET status='closed', status_reason=?, closed=? WHERE batch_id=?", (f"abandoned: {why}"[:300], now(), bid))
        batches._event(con, bid, "abandoned", why)
        batches._event(con, bid, "closed", why)
        con.execute("DELETE FROM lease WHERE resource=?", (f"batch:{bid}",))


def settle(con, bid: str, stale_days: float | None = None) -> str | None:
    """How a production / rework run with nothing alive ends. Returns what was done, or None (leave it)."""
    stale_days = float(config().get("stale_days", 7) if stale_days is None else stale_days)
    b = batches.get(con, bid)
    if b["status"] in ("done", "closed", "failed"):
        return None
    n = b["n_cases"] or len(_cases(con, bid))
    got = results_of(con, b)
    if n and len(got) >= n:
        _finish(con, bid, f"results complete ({len(got)} of {n}); the run ended before its stages were closed")
        return f"{bid}: done, results complete"
    by = superseded_by(con, b)
    if by:
        _close(con, bid, f"superseded by {by} (a later run of the same cases)")
        return f"{bid}: closed, superseded by {by}"
    idle = batches.idle_hours(con, bid)
    if idle is not None and idle / 24 >= stale_days:
        if got:
            _finish(con, bid, f"partial: {len(got)} of {n} cases have results; nothing ran for {idle / 24:.0f} days")
            return f"{bid}: done, partial ({len(got)}/{n})"
        _close(con, bid, f"stopped: no result and nothing ran for {idle / 24:.0f} days")
        return f"{bid}: closed, stopped"
    return None
