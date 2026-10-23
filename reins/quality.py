"""How good each released version really was, measured on what people said about its results.

User 2026-10-23: the system should evolve from the feedback on its deliveries -- and show that it does. Every human
verdict (a reviewer in the review platform, or the client: Landmark's QA, HM Land Registry's samples) is attributed
to the run that produced what was judged: the newest production / rework batch that held the case and existed before
the verdict. That batch's release is the version. Per release, case by case (a case's worst verdict counts once):

  checked_auto   cases the run finished without a person that someone has since judged
  wrong_auto     ... and judged wrong: a miss, the error the client may see
  miss_rate      wrong_auto / checked_auto
  checked_human  cases the run sent to a person, judged; wrong_human: and really wrong (how useful the flag was)
  auto_share     the run's cases finished without a person (results value in [results] good)

and the same per week of the verdict. A falling miss_rate at a steady or rising auto_share is the system getting
better. Verdicts on a case no reins batch held before them (deliveries made before reins) are counted as unattributed.

The watchdog refreshes it daily (verdicts imported for every production / rework batch of the last DAYS days first);
`reins quality` shows it; the production page draws it.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from .store import now, tx

STRICT = ("production", "rework")
DAYS = 120
EVERY_H = 24
MIN_N = 30                     # fewer auto-passed cases checked than this: the rate is shown, marked low_n


def _week(at: str) -> str:
    try:
        y, w, _ = dt.date.fromisoformat(at[:10]).isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return "?"


def compute(con, project: str) -> dict:
    from . import production
    from .paths import work_dir
    spec = production._cfg(project).get("results") or {}
    good = set(spec.get("good") or [])
    bats = con.execute(f"SELECT batch_id, created, release_name, workflow, scope, work_dir FROM batch WHERE project=? AND type IN"
                       f" ({','.join('?' * len(STRICT))}) ORDER BY created", (project, *STRICT)).fetchall()
    version = {b["batch_id"]: b["release_name"] or b["workflow"] or b["batch_id"] for b in bats}
    created = {b["batch_id"]: b["created"] for b in bats}
    held: dict[str, list[str]] = defaultdict(list)          # case -> batches holding it, oldest first
    for b in bats:
        for (c,) in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (b["batch_id"],)):
            held[c].append(b["batch_id"])
    results: dict[str, dict] = {}
    for b in bats:
        wd = work_dir(b)
        results[b["batch_id"]] = production._results(Path(wd) / spec["file"], spec) if spec.get("file") and wd else {}
    seen, verdicts = set(), []
    for r in con.execute("SELECT e.case_id, e.reviewer, e.at, e.verdict FROM review_event e JOIN batch b ON b.batch_id=e.batch_id"
                         " WHERE b.project=? AND e.action='verdict'", (project,)):
        k = (r[0], r[1], r[2], r[3])
        if k not in seen:
            seen.add(k)
            verdicts.append(k)
    rank = {"wrong": 2, "unsure": 1, "correct": 0}
    per_case: dict[tuple[str, str], dict] = {}               # (batch, case) -> worst verdict, sources, week
    unattributed = 0
    for case, reviewer, at, verdict in verdicts:
        before = [b for b in held.get(case, []) if created[b] <= (at or "")]
        if not before:
            unattributed += 1
            continue
        bid = before[-1]
        d = per_case.setdefault((bid, case), {"verdict": verdict, "src": set(), "week": _week(at or ""), "at": at})
        d["src"].add("client" if reviewer.startswith("client:") else "reviewer")
        if rank.get(verdict, 0) > rank.get(d["verdict"], 0):
            d["verdict"] = verdict
        if (at or "") > (d["at"] or ""):
            d["at"], d["week"] = at, _week(at)
    rel: dict[str, dict] = {}
    weeks: dict[tuple[str, str], dict] = {}
    for b in bats:
        v = version[b["batch_id"]]
        x = rel.setdefault(v, {"release": v, "first": b["created"], "batches": 0, "scopes": set(), "cases": 0, "auto": 0,
                               "checked_auto": 0, "wrong_auto": 0, "checked_human": 0, "wrong_human": 0,
                               "client_checked": 0, "client_wrong": 0})
        x["batches"] += 1
        x["scopes"].add(b["scope"])
        res = results[b["batch_id"]]
        x["cases"] += len(res)
        x["auto"] += sum(1 for val, _ in res.values() if val in good)
    for (bid, case), d in per_case.items():
        res = results.get(bid, {})
        if case not in res or d["verdict"] == "unsure":
            continue
        auto = res[case][0] in good
        x = rel[version[bid]]
        wrong = d["verdict"] == "wrong"
        if auto:
            x["checked_auto"] += 1
            x["wrong_auto"] += wrong
            w = weeks.setdefault((d["week"], version[bid]), {"week": d["week"], "release": version[bid], "checked_auto": 0, "wrong_auto": 0})
            w["checked_auto"] += 1
            w["wrong_auto"] += wrong
        else:
            x["checked_human"] += 1
            x["wrong_human"] += wrong
        if "client" in d["src"]:
            x["client_checked"] += 1
            x["client_wrong"] += wrong
    out = []
    import re as _re
    dated = lambda r: (_re.search(r"(\d{8})(?:-(\d+))?", r["release"]) or None)
    order = lambda r: (dated(r).group(1), int(dated(r).group(2) or 0)) if dated(r) else (r["first"][:10].replace("-", ""), 0)
    for x in sorted(rel.values(), key=order):                 # releases by their date (qa-YYYYMMDD-N), else first batch
        x["scopes"] = sorted(x["scopes"])
        x["auto_share"] = round(x["auto"] / x["cases"], 3) if x["cases"] else None
        x["miss_rate"] = round(x["wrong_auto"] / x["checked_auto"], 4) if x["checked_auto"] else None
        x["flag_precision"] = round(x["wrong_human"] / x["checked_human"], 3) if x["checked_human"] else None
        x["low_n"] = x["checked_auto"] < MIN_N
        out.append(x)
    return {"project": project, "at": now(), "releases": out,
            "weeks": sorted(weeks.values(), key=lambda w: (w["week"], w["release"])), "unattributed": unattributed,
            "verdicts": len(verdicts)}


def refresh(con, project: str | None = None) -> list[dict]:
    """Import verdicts for every recent production / rework batch, compute, cache. Returns the results."""
    from . import loop
    since = (dt.datetime.now() - dt.timedelta(days=DAYS)).isoformat(timespec="seconds")
    projects = [project] if project else [r[0] for r in con.execute(
        f"SELECT DISTINCT project FROM batch WHERE type IN ({','.join('?' * len(STRICT))})", STRICT)]
    out = []
    for p in projects:
        if not loop._cfg(p).get("results"):
            continue
        for (bid,) in con.execute(f"SELECT batch_id FROM batch WHERE project=? AND type IN ({','.join('?' * len(STRICT))})"
                                  f" AND created>?", (p, *STRICT, since)).fetchall():
            try:
                loop.import_reviews(con, bid)
            except Exception:                                                   # noqa: BLE001 -- one batch never stops it
                pass
        q = compute(con, p)
        with tx(con):
            con.execute("INSERT INTO quality_cache (project, at, data) VALUES (?,?,?)", (p, q["at"], json.dumps(q)))
            con.execute("DELETE FROM quality_cache WHERE project=? AND id NOT IN (SELECT id FROM quality_cache WHERE project=?"
                        " ORDER BY id DESC LIMIT 30)", (p, p))
        out.append(q)
    return out


def latest(con, project: str | None = None) -> list[dict]:
    rows = con.execute("SELECT project, data FROM quality_cache WHERE id IN (SELECT MAX(id) FROM quality_cache GROUP BY project)"
                       + (" AND project=?" if project else ""), ((project,) if project else ())).fetchall()
    return [json.loads(r[1]) for r in rows]


def tend(con) -> list[str]:
    """Daily, from the watchdog."""
    last = con.execute("SELECT MAX(at) FROM quality_cache").fetchone()[0]
    if last and dt.datetime.fromisoformat(last) > dt.datetime.now() - dt.timedelta(hours=EVERY_H):
        return []
    qs = refresh(con)
    return [f"quality {q['project']}: {len(q['releases'])} releases, {q['verdicts']} verdicts" for q in qs]
