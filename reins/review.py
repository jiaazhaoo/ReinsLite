"""C6 human review records: append-only, signed, with the fields the project's taxonomy requires.

    reins review assign BATCH --reviewer NAME --queue Q1 --cases a,b,c [--round 'Q1 2026-10-07']
    reins review verdict BATCH CASE --reviewer NAME correct|wrong|unsure [--error-type T] [--note '...'] [--target polygon]
    reins review import BATCH --file events.jsonl                 bulk events from a review platform
    reins review calibration BATCH                                 reviewers compared on the same queues and cases
    reins review candidates BATCH --out labels.jsonl               human verdicts as reviewer_verdict labels for the benchmark
    reins review taxonomy [review.toml]

Project review.toml:
    version = 2
    error_types = ["location", "polygon_extent", "wrong_plan", "no_evidence"]
    unsure_needs_note = true
"""
from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path

from . import batches
from .store import ReinsError, now, tx

VERDICTS = ("correct", "wrong", "unsure")
GRADE_TO_VERDICT = {"P": "correct", "A": "unsure", "F": "wrong"}


def taxonomy(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"version": 0, "error_types": [], "unsure_needs_note": True}
    t = tomllib.loads(path.read_text(encoding="utf-8"))
    if "version" not in t or "error_types" not in t:
        raise ReinsError(f"{path}: needs version = N and error_types = [...]")
    return {"unsure_needs_note": True, **t}


def _check(ev: dict, tax: dict, members: set[str]) -> str | None:
    if ev.get("case_id") not in members:
        return f"{ev.get('case_id')!r} is not in this batch"
    if not ev.get("reviewer", "").strip():
        return "reviewer is required (append-only records are signed)"
    if ev.get("action") not in ("view", "verdict", "draw", "note"):
        return f"action {ev.get('action')!r}"
    if ev["action"] == "verdict":
        v = ev.get("verdict")
        if v not in VERDICTS:
            return f"verdict {v!r} not in {VERDICTS}"
        if v == "wrong":
            et = ev.get("error_type")
            if not et:
                return "wrong needs error_type"
            if tax["error_types"] and et not in tax["error_types"]:
                return f"error_type {et!r} not in taxonomy v{tax['version']}: {tax['error_types']}"
        if v == "unsure" and tax.get("unsure_needs_note", True) and not (ev.get("note") or "").strip():
            return "unsure needs a note (what would settle it)"
    return None


def record(con, batch: str, events: list[dict], tax: dict, key: str | None = None) -> int:
    batches.get(con, batch)
    for ev in events:                                     # the project's own name for the case key -> case_id
        if "case_id" not in ev and key and key in ev:
            ev["case_id"] = ev.pop(key)
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (batch,))}
    errors = [f"event {i}: {e}" for i, ev in enumerate(events, 1) if (e := _check(ev, tax, members))]
    if errors:
        raise ReinsError(f"{len(errors)} problems, nothing recorded:\n  " + "\n  ".join(errors[:20]))
    with tx(con):
        con.executemany(
            "INSERT INTO review_event (at, batch_id, case_id, target, reviewer, action, verdict, error_type, note,"
            " payload, taxonomy_version, client) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(ev.get("at") or now(), batch, ev["case_id"], ev.get("target", "case"), ev["reviewer"], ev["action"],
              ev.get("verdict"), ev.get("error_type"), ev.get("note"),
              json.dumps(ev["payload"], ensure_ascii=False) if ev.get("payload") is not None else None,
              tax["version"], ev.get("client")) for ev in events])
    return len(events)


def assign(con, batch: str, reviewer: str, cases: list[str], queue: str | None, round_: str | None, by: str) -> int:
    """Append-only: new cases go after the reviewer's existing list; nothing already assigned is reordered."""
    batches.get(con, batch)
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (batch,))}
    bad = [c for c in cases if c not in members]
    if bad:
        raise ReinsError(f"not in {batch}: {bad[:10]}")
    with tx(con):
        start = con.execute("SELECT COALESCE(MAX(ord),0) FROM review_assignment WHERE batch_id=? AND reviewer=?",
                            (batch, reviewer)).fetchone()[0]
        con.executemany("INSERT INTO review_assignment (at, batch_id, case_id, reviewer, queue, round, ord, by_whom)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        [(now(), batch, c, reviewer, queue, round_, start + i, by) for i, c in enumerate(cases, 1)])
    return len(cases)


def current_verdicts(con, batch: str) -> dict[tuple[str, str, str], dict]:
    """Latest verdict per (case, target, reviewer)."""
    out = {}
    for r in con.execute("SELECT * FROM review_event WHERE batch_id=? AND action='verdict' ORDER BY id", (batch,)):
        out[(r["case_id"], r["target"], r["reviewer"])] = dict(r)
    return out


def calibration(con, batch: str) -> dict:
    """Per reviewer: wrong rate per queue; and pairwise disagreement on cases two reviewers both judged."""
    verd = current_verdicts(con, batch)
    queue_of = {}
    for r in con.execute("SELECT case_id, reviewer, queue FROM review_assignment WHERE batch_id=? ORDER BY id", (batch,)):
        queue_of[(r["case_id"], r["reviewer"])] = r["queue"] or "-"
    per = defaultdict(lambda: defaultdict(lambda: {"n": 0, "wrong": 0, "unsure": 0}))
    for (case, target, rev), v in verd.items():
        q = queue_of.get((case, rev), "-")
        d = per[rev][q]
        d["n"] += 1
        d[v["verdict"]] = d.get(v["verdict"], 0) + 1 if v["verdict"] != "correct" else d.get("correct", 0)
    by_case = defaultdict(dict)
    for (case, target, rev), v in verd.items():
        by_case[(case, target)][rev] = v["verdict"]
    pairs = defaultdict(lambda: {"both": 0, "disagree": 0, "cases": []})
    for (case, target), revs in by_case.items():
        names_ = sorted(revs)
        for i in range(len(names_)):
            for j in range(i + 1, len(names_)):
                k = (names_[i], names_[j])
                pairs[k]["both"] += 1
                if revs[names_[i]] != revs[names_[j]]:
                    pairs[k]["disagree"] += 1
                    pairs[k]["cases"].append(case)
    rates = {rev: {q: {**d, "wrong_rate": round(d["wrong"] / d["n"], 3) if d["n"] else None} for q, d in qs.items()}
             for rev, qs in per.items()}
    return {"reviewers": rates, "pairs": [{"a": a, "b": b, **v} for (a, b), v in pairs.items()]}


def candidates(con, batch: str) -> list[dict]:
    """Human verdicts as benchmark labels (source reviewer_verdict). A person promotes them to golden on the review page."""
    out = []
    for (case, target, rev), v in sorted(current_verdicts(con, batch).items()):
        out.append({"case_id": case, "target": target, "value": v["verdict"], "source": "reviewer_verdict",
                    "by": rev, "saw_drawing": None, "note": v["note"] or v["error_type"] or "", "batch": batch,
                    "at": v["at"]})
    return out


def read_events(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
