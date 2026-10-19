"""C5 gate results.

An end-to-end gate scores the two primary metrics every project shares: missed_error (must not increase) and
review_load (a cost). A module gate (2026-10-19) scores the module on its own benchmark with its own metrics: each one
has a direction and is hard (may not get worse than the base) or soft (reported only).

A project's gate command writes this JSON to $REINS_GATE_OUT:
    {"benchmark": "bench-demo-v6", "tiers": "units,judges", "stages_covered": "judges,lanes",
     "missed_error": 0, "review_load": 93, "base_missed_error": 0, "base_review_load": 96,
     "golden_regressions": 0, "diff_path": "/path/to/diff.html"}
or, for a module gate,
    {"benchmark": "bench-demo-crops-v3", "tiers": "module,downstream", "stages_covered": "crops",
     "metrics": {"plans_whole": {"value": 47, "base": 36, "better": "higher", "hard": true, "split": "dev"},
                 "plans_cut":   {"value": 0, "base": 6, "better": "lower", "hard": true, "split": "dev"},
                 "boxes_per_page": {"value": 1.8, "base": 1.3, "better": "lower", "hard": false}},
     "golden_regressions": 0, "cases_rerun": 22, "cases_reused": 337}
missed_error / review_load may be left out of a module gate (they are then recorded as 0).
"""
from __future__ import annotations

import json
import re

from . import modules
from .store import ReinsError, now, session, tx


def worse(m: dict) -> bool:
    """One metric {value, base, better: higher|lower} is worse than its base."""
    v, b = m.get("value"), m.get("base")
    if v is None or b is None:
        return False
    if m.get("better", "higher") not in ("higher", "lower"):
        raise ReinsError(f"metric direction {m.get('better')!r}: use higher or lower")
    return v < b if m.get("better", "higher") == "higher" else v > b


def record(con, version: str, *, benchmark: str, tiers: str, stages_covered: str, missed_error: int = 0,
           review_load: int = 0, base_missed_error: int = 0, base_review_load: int = 0, golden_regressions: int = 0,
           diff_path: str | None = None, skipped: str | None = None, metrics: dict | None = None,
           module: str | None = None, cases_rerun: int | None = None, cases_reused: int | None = None) -> dict:
    if not benchmark.startswith("bench-"):
        raise ReinsError(f"benchmark {benchmark!r} must be named bench-<name>-vN")
    metrics = metrics or {}
    for k, m in metrics.items():
        if not isinstance(m, dict) or "value" not in m:
            raise ReinsError(f"metric {k}: needs at least {{value, base, better, hard}}")
    hard_worse = sorted(k for k, m in metrics.items() if m.get("hard") and worse(m))
    status = "green" if missed_error <= base_missed_error and golden_regressions == 0 and not hard_worse else "red"
    m = re.match(r"^(bench-.+)-v(\d+)$", benchmark)
    with tx(con):
        modules.get(con, version)
        if m:
            brow = con.execute("SELECT status, holdout_compromised FROM benchmark WHERE name=? AND version=?",
                               (m.group(1), int(m.group(2)))).fetchone()
            if brow and brow["status"] != "frozen":
                raise ReinsError(f"{benchmark} is registered but not frozen; a gate runs on frozen benchmarks only")
            if brow and brow["holdout_compromised"]:
                skipped = f"{skipped + '; ' if skipped else ''}holdout compromised (tuned on): result is dev-only evidence"
        con.execute("INSERT INTO gate_log (at, version, benchmark, tiers, stages_covered, missed_error, review_load,"
                    " base_missed_error, base_review_load, golden_regressions, status, diff_path, skipped, session,"
                    " module, metrics, cases_rerun, cases_reused) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now(), version, benchmark, tiers, stages_covered, missed_error, review_load, base_missed_error,
                     base_review_load, golden_regressions, status, diff_path, skipped, session(), module,
                     json.dumps(metrics) if metrics else None, cases_rerun, cases_reused))
        detail = f"{benchmark} {tiers}: " + (", ".join(f"{k} {m['value']}/{m.get('base')}" for k, m in metrics.items())
                                            if metrics else f"missed_error {missed_error}/{base_missed_error}"
                                            f" review_load {review_load}/{base_review_load}")
        if cases_rerun is not None:
            detail += f"; downstream re-ran {cases_rerun}, reused {cases_reused}"
        con.execute("INSERT INTO module_event (at, version, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), version, f"gate_{status}", detail, session()))
    return {"status": status, "missed_error": missed_error, "base_missed_error": base_missed_error,
            "review_load": review_load, "base_review_load": base_review_load, "golden_regressions": golden_regressions,
            "metrics": metrics, "hard_worse": hard_worse, "cases_rerun": cases_rerun, "cases_reused": cases_reused}


def latest(con, version: str):
    return con.execute("SELECT * FROM gate_log WHERE version=? ORDER BY id DESC LIMIT 1", (version,)).fetchone()
