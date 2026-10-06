"""C5 gate results. Two primary metrics everywhere: missed_error (must not increase) and review_load (a cost).

A project's gate command writes this JSON to $REINS_GATE_OUT:
    {"benchmark": "bench-sheffield-wp3-359-v6", "tiers": "units,judges", "stages_covered": "judges,lanes",
     "missed_error": 0, "review_load": 93, "base_missed_error": 0, "base_review_load": 96,
     "golden_regressions": 0, "diff_path": "/data/.../diff.html"}
"""
from __future__ import annotations

from . import modules
from .store import ReinsError, now, session, tx


def record(con, version: str, *, benchmark: str, tiers: str, stages_covered: str, missed_error: int, review_load: int,
           base_missed_error: int, base_review_load: int, golden_regressions: int = 0, diff_path: str | None = None,
           skipped: str | None = None) -> dict:
    if not benchmark.startswith("bench-"):
        raise ReinsError(f"benchmark {benchmark!r} must be named bench-<name>-vN")
    status = "green" if missed_error <= base_missed_error and golden_regressions == 0 else "red"
    with tx(con):
        modules.get(con, version)
        con.execute("INSERT INTO gate_log (at, version, benchmark, tiers, stages_covered, missed_error, review_load,"
                    " base_missed_error, base_review_load, golden_regressions, status, diff_path, skipped, session)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now(), version, benchmark, tiers, stages_covered, missed_error, review_load, base_missed_error,
                     base_review_load, golden_regressions, status, diff_path, skipped, session()))
        con.execute("INSERT INTO module_event (at, version, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), version, f"gate_{status}", f"{benchmark} {tiers}: missed_error {missed_error}/{base_missed_error}"
                     f" review_load {review_load}/{base_review_load}", session()))
    return {"status": status, "missed_error": missed_error, "base_missed_error": base_missed_error,
            "review_load": review_load, "base_review_load": base_review_load, "golden_regressions": golden_regressions}


def latest(con, version: str):
    return con.execute("SELECT * FROM gate_log WHERE version=? ORDER BY id DESC LIMIT 1", (version,)).fetchone()
