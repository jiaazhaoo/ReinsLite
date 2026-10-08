"""C7b routing rules as data, and the lane diff every rule change must produce.

A project's rules.toml:
    [[rule]]
    id = "no_evidence_never_auto_accept"
    lane = "review"
    when = "the case has no source evidence"
    evidence = "bench-demo-v3: 491 cases were auto-accepted without evidence"
    decided = "2026-10-05"
    by = "user"
    cost_per_case_usd = 0.0
"""
from __future__ import annotations

import csv
import json
import tomllib
from collections import Counter
from pathlib import Path

from . import names
from .store import ReinsError, now, session, tx

REQUIRED = ("id", "lane", "when", "evidence", "decided", "by")


def load(path: Path) -> list[dict]:
    if not path.is_file():
        raise ReinsError(f"{path} not found")
    rules = tomllib.loads(path.read_text(encoding="utf-8")).get("rule", [])
    ids = Counter(r.get("id") for r in rules)
    problems = [f"rule #{i}: missing {k}" for i, r in enumerate(rules, 1) for k in REQUIRED if not r.get(k)]
    problems += [f"rule id {k!r} appears {n} times" for k, n in ids.items() if n > 1]
    if problems:
        raise ReinsError(f"{path}: " + "; ".join(problems))
    return rules


def describe(rules: list[dict]) -> str:
    lines = []
    for r in rules:
        if r.get("status", "active") != "active":
            continue
        lines.append(f"{r['id']:<36} -> {r['lane']:<14} when {r['when']}")
        lines.append(f"{'':<36}    evidence: {r['evidence']}  (decided {r['decided']} by {r['by']})")
    return "\n".join(lines)


def _lanes(path: Path, key: str | None, lane_col: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        key = names.case_field(rd.fieldnames, key) or key or names.CASE_KEY
        if key not in (rd.fieldnames or []) or lane_col not in rd.fieldnames:
            raise ReinsError(f"{path}: needs columns {key!r} and {lane_col!r}; has {rd.fieldnames}")
        out = {}
        for r in rd:
            if r[key] in out:
                raise ReinsError(f"{path}: {r[key]!r} appears twice")
            out[r[key]] = r[lane_col]
    return out


def diff(old: Path, new: Path, key: str | None = None, lane_col: str = "lane") -> dict:
    """Which cases moved from which lane to which. Required before a rule change takes effect."""
    a, b = _lanes(old, key, lane_col), _lanes(new, key, lane_col)
    moves: dict[tuple[str, str], list[str]] = {}
    for k in sorted(set(a) | set(b)):
        la, lb = a.get(k, "(absent)"), b.get(k, "(absent)")
        if la != lb:
            moves.setdefault((la, lb), []).append(k)
    return {"old": str(old), "new": str(new), "n_old": len(a), "n_new": len(b),
            "unchanged": sum(1 for k in a if a[k] == b.get(k)),
            "moves": [{"from": f, "to": t, "n": len(c), "cases": c} for (f, t), c in sorted(moves.items())]}


def freeze(con, path: Path, project: str, old_lanes: Path | None, new_lanes: Path | None, key: str | None = None,
           lane_col: str = "lane") -> dict:
    """ruleset-<project>-vN for the rules file as it is now. From v2 on, a lane diff (old vs new lanes) is required:
    a rule change without the list of cases that moved is not allowed to take effect."""
    rules = load(path)
    sha = names.sha256_file(path)
    with tx(con):
        same = con.execute("SELECT name FROM ruleset WHERE project=? AND sha=?", (project, sha)).fetchone()
        if same:
            return {"name": same["name"], "new": False}
        v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM ruleset WHERE project=?", (project,)).fetchone()[0]
        diff_path = summary = None
        if v > 1:
            if not (old_lanes and new_lanes):
                raise ReinsError(f"ruleset v{v} needs --old-lanes and --new-lanes (the lane diff that justifies the change)")
            d = diff(old_lanes, new_lanes, key, lane_col)
            diff_path = str(path.parent / f"ruleset-{project}-v{v}.lanediff.json")
            Path(diff_path).write_text(json.dumps(d, ensure_ascii=False, indent=1))
            summary = "; ".join(f"{m['from']}->{m['to']}:{m['n']}" for m in d["moves"]) or "no case moved"
        name = f"ruleset-{project}-v{v}"
        con.execute("INSERT INTO ruleset (name, project, version, sha, path, n_rules, diff_path, diff_summary, created, session)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)", (name, project, v, sha, str(path.resolve()), len(rules), diff_path, summary, now(), session()))
    return {"name": name, "new": True, "n_rules": len(rules), "diff_summary": summary, "diff_path": diff_path}


def get(con, name: str):
    row = con.execute("SELECT * FROM ruleset WHERE name=?", (name,)).fetchone()
    if not row:
        raise ReinsError(f"ruleset {name!r} is not registered (reins rules freeze)")
    return row


def format_diff(d: dict, show: int = 10) -> str:
    lines = [f"{d['n_old']} -> {d['n_new']} cases, {d['unchanged']} unchanged"]
    for m in d["moves"]:
        lines.append(f"  {m['from']:<14} -> {m['to']:<14} {m['n']:>6}   {', '.join(m['cases'][:show])}"
                     + (" ..." if m["n"] > show else ""))
    return "\n".join(lines)
