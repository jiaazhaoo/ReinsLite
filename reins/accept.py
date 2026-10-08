"""C11 acceptance: a fixed-seed sample from the auto-accepted lane, graded P/A/F, judged against an error budget.

    reins accept sample BATCH --lanes lanes.csv [--n 315 --seed 20261007 --from-lane auto_accept --ideal 3 --max 6]
    reins accept grade BATCH --file grades.tsv            case_id, grade (P/A/F), by[, note]
    reins accept check BATCH --lanes lanes.csv            which sampled cases changed lane since the sample (stale)
    reins accept decide BATCH --by USER [--note '...']     ship | ship_with_note | rework, recorded
    reins accept show BATCH

Decision: F <= ideal -> ship; ideal < F <= max -> ship_with_note (add rules, resample before the next package);
F > max -> rework. Grades on a stale sample do not count.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from . import batches, names
from .store import ReinsError, now, tx


def _lanes(path: Path, key: str | None = None, col="lane") -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        key = names.case_field(rd.fieldnames, key) or key or names.CASE_KEY
        if key not in (rd.fieldnames or []) or col not in rd.fieldnames:
            raise ReinsError(f"{path}: needs columns {key!r} and {col!r}; has {rd.fieldnames}")
        return {r[key]: r[col] for r in rd}


def _current(con, batch: str):
    row = con.execute("SELECT * FROM acceptance WHERE batch_id=? ORDER BY id DESC LIMIT 1", (batch,)).fetchone()
    if not row:
        raise ReinsError(f"no acceptance sample for {batch}: reins accept sample ...")
    return row


def sample(con, batch: str, lanes_file: Path, n: int = 315, seed: int = 20261007, from_lane: str = "auto_accept",
           ideal: int = 3, max_: int = 6, key: str | None = None) -> dict:
    batches.get(con, batch)
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (batch,))}
    lanes = _lanes(lanes_file, key)
    extra = sorted(set(lanes) - members)
    if extra:
        raise ReinsError(f"lanes file has {len(extra)} cases not in {batch} (first: {extra[:5]}): C1 conservation")
    pool = sorted(c for c, l in lanes.items() if l == from_lane)
    if not pool:
        raise ReinsError(f"no case in lane {from_lane!r}")
    rnd = random.Random(seed)
    picked = sorted(rnd.sample(pool, min(n, len(pool))))
    rec = [{"case_id": c, "lane": lanes[c]} for c in picked]
    with tx(con):
        cur = con.execute("INSERT INTO acceptance (at, batch_id, seed, n, from_lane, lanes_sha, sample, budget_ideal, budget_max)"
                          " VALUES (?,?,?,?,?,?,?,?,?)",
                          (now(), batch, seed, len(picked), from_lane, names.sha256_file(lanes_file), json.dumps(rec), ideal, max_))
    return {"acceptance_id": cur.lastrowid, "n": len(picked), "pool": len(pool), "seed": seed,
            "note": "pool smaller than n" if len(pool) < n else ""}


def check(con, batch: str, lanes_file: Path, key: str | None = None) -> dict:
    row = _current(con, batch)
    lanes = _lanes(lanes_file, key)
    stale = [s["case_id"] for s in json.loads(row["sample"]) if lanes.get(s["case_id"]) != s["lane"]]
    return {"acceptance_id": row["id"], "n": row["n"], "stale": stale}


def grade(con, batch: str, rows: list[tuple[str, str, str, str | None]]) -> int:
    row = _current(con, batch)
    if row["decision"]:
        raise ReinsError(f"acceptance #{row['id']} already decided ({row['decision']}); take a new sample")
    members = {s["case_id"] for s in json.loads(row["sample"])}
    errors = []
    for i, (c, g, by, _) in enumerate(rows, 1):
        if c not in members:
            errors.append(f"row {i}: {c!r} is not in the sample")
        if g not in ("P", "A", "F"):
            errors.append(f"row {i}: grade {g!r} is not P/A/F")
        if not by.strip():
            errors.append(f"row {i}: grader name required")
    if errors:
        raise ReinsError("\n  ".join([f"{len(errors)} problems, nothing recorded:"] + errors[:20]))
    with tx(con):
        con.executemany("INSERT INTO acceptance_grade (at, acceptance_id, case_id, grade, by_whom, note) VALUES (?,?,?,?,?,?)",
                        [(now(), row["id"], c, g, by, nt) for c, g, by, nt in rows])
    return len(rows)


def read_grades(path: Path, key: str | None = None) -> list[tuple[str, str, str, str | None]]:
    with path.open(encoding="utf-8-sig") as f:
        rd = csv.DictReader(f, delimiter="\t")
        k = names.case_field(rd.fieldnames, key)
        if not k or not {"grade", "by"} <= set(rd.fieldnames or []):
            raise ReinsError(f"{path}: needs columns case_id (or the project's case key), grade, by[, note]; has {rd.fieldnames}")
        return [(r[k], r["grade"].strip().upper(), r["by"], r.get("note")) for r in rd]


def tally(con, acceptance_id: int) -> dict:
    latest = {}
    for r in con.execute("SELECT case_id, grade FROM acceptance_grade WHERE acceptance_id=? ORDER BY id", (acceptance_id,)):
        latest[r["case_id"]] = r["grade"]
    return {"graded": len(latest), "P": sum(1 for g in latest.values() if g == "P"),
            "A": sum(1 for g in latest.values() if g == "A"), "F": sum(1 for g in latest.values() if g == "F")}


def decide(con, batch: str, by: str, note: str = "", lanes_file: Path | None = None, key: str | None = None) -> dict:
    row = _current(con, batch)
    if row["decision"]:
        raise ReinsError(f"already decided: {row['decision']} by {row['decided_by']} at {row['decided_at']}")
    if lanes_file is not None:
        st = check(con, batch, lanes_file)["stale"]
        if st:
            raise ReinsError(f"{len(st)} sampled cases changed lane since the sample (first: {st[:5]}); resample")
    t = tally(con, row["id"])
    if t["graded"] < row["n"]:
        raise ReinsError(f"only {t['graded']}/{row['n']} sampled cases graded")
    f = t["F"]
    decision = "ship" if f <= row["budget_ideal"] else "ship_with_note" if f <= row["budget_max"] else "rework"
    with tx(con):
        con.execute("UPDATE acceptance SET decision=?, decided_at=?, decided_by=?, note=? WHERE id=?",
                    (decision, now(), by, note, row["id"]))
    return {"decision": decision, **t, "budget_ideal": row["budget_ideal"], "budget_max": row["budget_max"],
            "registry_line": json.dumps({"ts": now(), "batch_id": batch, "acceptance_id": row["id"], "decision": decision,
                                         "sample_n": row["n"], "F": f, "A": t["A"], "by": by})}


def show(con, batch: str) -> dict:
    row = _current(con, batch)
    return {**{k: row[k] for k in ("id", "at", "seed", "n", "from_lane", "budget_ideal", "budget_max", "decision",
                                     "decided_by", "decided_at", "note")}, **tally(con, row["id"])}
