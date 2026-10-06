"""C7b routing rules as data, and the lane diff every rule change must produce.

A project's rules.toml:
    [[rule]]
    id = "no_plan_never_auto_accept"
    lane = "review"
    when = "the case has no plan_image"
    evidence = "bench-sheffield-wp3-359-v3, 491 cases were auto-accepted without a drawing"
    decided = "2026-10-05"
    by = "user"
    cost_per_case_usd = 0.0
"""
from __future__ import annotations

import csv
import tomllib
from collections import Counter
from pathlib import Path

from .store import ReinsError

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


def _lanes(path: Path, key: str, lane_col: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        if key not in (rd.fieldnames or []) or lane_col not in rd.fieldnames:
            raise ReinsError(f"{path}: needs columns {key!r} and {lane_col!r}; has {rd.fieldnames}")
        out = {}
        for r in rd:
            if r[key] in out:
                raise ReinsError(f"{path}: {r[key]!r} appears twice")
            out[r[key]] = r[lane_col]
    return out


def diff(old: Path, new: Path, key: str = "oachargeid", lane_col: str = "lane") -> dict:
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


def format_diff(d: dict, show: int = 10) -> str:
    lines = [f"{d['n_old']} -> {d['n_new']} cases, {d['unchanged']} unchanged"]
    for m in d["moves"]:
        lines.append(f"  {m['from']:<14} -> {m['to']:<14} {m['n']:>6}   {', '.join(m['cases'][:show])}"
                     + (" ..." if m["n"] > show else ""))
    return "\n".join(lines)
