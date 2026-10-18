"""The production page (board /production): what a manager looks at. Production and rework batches only, one block per
work package (batch scope): how many cases have a result, what the results are, why the others could not be processed,
what it cost and when it should be done. No commands, sessions, versions or paths.

A project says where a case's result lives in its reins.toml (optional; without it the page counts finished stages):

    [results]
    file = "out/results.csv"          # relative to the batch work_dir
    key = "case"                      # the case column
    key_pattern = "([0-9]+)$"         # optional: the case id inside the key (SHF_100047 -> 100047)
    value = "lane"                    # the result column
    detail = "category"               # optional: a breakdown of the value
    good = ["auto pass"]              # the values that need nobody
    [[results.reasons]]               # a skip / fail reason -> a word (first match wins)
    match = "no scans|no source"
    zh = "..."                        # any language keys the page offers
    en = "No scans"
    [labels]                          # stage names, result values, details -> words per language
    lane_a = { zh = "...", en = "..." }

A case's result is taken from the newest batch of the work package that has a row for it; rows of cases outside the
batch are ignored (a results file may hold more than its batch: conservation, C1).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path

from . import projects

TYPES = ("production", "rework")
_FILES: dict[str, tuple[float, dict]] = {}


def _cfg(project: str) -> dict:
    for _, c in projects.all_():
        if c.get("project") == project:
            return c
    return {}


def _results(path: Path, spec: dict) -> dict[str, tuple[str, str]]:
    """case_id -> (value, detail), cached by mtime."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return {}
    key = str(path)
    if key in _FILES and _FILES[key][0] == mt:
        return _FILES[key][1]
    pat = re.compile(spec["key_pattern"]) if spec.get("key_pattern") else None
    out = {}
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                k = (row.get(spec.get("key", "case_id")) or "").strip()
                if pat:
                    m = pat.search(k)
                    k = m.group(1) if m else k
                v = (row.get(spec.get("value", "result")) or "").strip()
                if k and v:
                    out[k] = (v, (row.get(spec["detail"]) or "").strip() if spec.get("detail") else "")
    except OSError:
        return {}
    _FILES[key] = (mt, out)
    return out


def _reason_word(reason: str, rules: list[dict]) -> str:
    for i, r in enumerate(rules):
        try:
            if re.search(r["match"], reason or "", re.I):
                return f"r{i}"
        except re.error:
            continue
    return "other"


def _pause_word(reason: str | None) -> str:
    """reins' own pause reasons, in a manager's words (the words are keys the page translates)."""
    r = (reason or "").lower()
    if "disk" in r:
        return "disk_full"
    if "memory" in r or "killed" in r:
        return "out_of_memory"
    if "exited" in r or "error" in r:
        return "program_error"
    if "cap" in r or "spend" in r or "budget" in r:
        return "budget"
    if r.startswith("等") or "wait" in r:
        return "waiting"
    return "other"


def _part_state(con, b) -> tuple[str, str | None]:
    """(state, the newest sign of work: an outcome or a process start/end)."""
    from .board import _activity, ACTIVE_H
    if b["status"] in ("done", "closed"):
        return "done", None
    if b["status"] == "paused":
        return "paused", None
    if b["status"] == "open":
        return "waiting", None
    a = _activity(con, b["batch_id"])
    recent = a["progress"] and (dt.datetime.now() - dt.datetime.fromisoformat(a["progress"])).total_seconds() < ACTIVE_H * 3600
    return ("running" if a["alive"] or recent else "stopped"), a["at"]


def _abandoned(con, bid: str) -> bool:
    return bool(con.execute("SELECT 1 FROM batch_event WHERE batch_id=? AND event='abandoned'", (bid,)).fetchone())


def view(con) -> dict:
    from .board import _eta
    rows = [b for b in con.execute("SELECT * FROM batch WHERE type IN (%s) ORDER BY created" % ",".join("?" * len(TYPES)), TYPES)
            if b["status"] != "failed" and not _abandoned(con, b["batch_id"])]      # failed = superseded or never ran
    spent = {r[0]: r[1] for r in con.execute("SELECT batch_id, SUM(amount) FROM spend GROUP BY batch_id")}
    labels, reasons_cfg, packages = {}, {}, {}
    for b in rows:
        packages.setdefault((b["project"], b["scope"]), []).append(b)
    now = dt.datetime.now()
    day_ago = (now - dt.timedelta(hours=24)).isoformat(timespec="seconds")
    daily: dict[str, int] = {}
    out = []
    for (project, scope), parts in packages.items():
        cfg = _cfg(project)
        spec = cfg.get("results") or {}
        rules = spec.get("reasons") or []
        if project not in labels:
            labels[project] = cfg.get("labels") or {}
            reasons_cfg[project] = [{k: v for k, v in r.items() if k != "match"} for r in rules]
        good_vals = set(spec.get("good") or [])
        ids = [p["batch_id"] for p in parts]
        q = ",".join("?" * len(ids))
        members: dict[str, set] = {p["batch_id"]: set() for p in parts}
        for bid, c in con.execute(f"SELECT batch_id, case_id FROM batch_case WHERE batch_id IN ({q})", ids):
            members[bid].add(c)
        cases = set().union(*members.values())
        stage_order = [r[0] for r in con.execute("SELECT stage FROM batch_stage WHERE batch_id=? ORDER BY ord", (parts[-1]["batch_id"],))]
        # newest outcome per (stage, case) across the parts; the first time a case finished the last stage
        outcome: dict[tuple[str, str], tuple[str, str]] = {}
        last_stage = stage_order[-1] if stage_order else None
        first_final: dict[str, str] = {}
        last_at = None
        for at, stage, case, status, reason in con.execute(
                f"SELECT at, stage, case_id, status, reason FROM case_event WHERE batch_id IN ({q}) AND status<>'started' ORDER BY id", ids):
            outcome[(stage, case)] = (status, reason or "")
            last_at = at if not last_at or at > last_at else last_at
            if stage == last_stage and case not in first_final:
                first_final[case] = at
        result: dict[str, tuple[str, str]] = {}
        if spec.get("file"):
            for p in sorted(parts, key=lambda p: p["created"], reverse=True):
                if not p["work_dir"]:
                    continue
                for c, v in _results(Path(p["work_dir"]) / spec["file"], spec).items():
                    if c in members[p["batch_id"]] and c not in result:
                        result[c] = v
        finished = {c for c in cases if c in result or (last_stage and (last_stage, c) in outcome)}
        by_value: dict[str, dict] = {}
        for c, (v, d) in result.items():
            e = by_value.setdefault(v, {"value": v, "n": 0, "good": v in good_vals, "details": {}})
            e["n"] += 1
            if d:
                e["details"][d] = e["details"].get(d, 0) + 1
        results = sorted(({**e, "details": [{"value": k, "n": n} for k, n in sorted(e["details"].items())]}
                          for e in by_value.values()), key=lambda e: (not e["good"], -e["n"]))
        # cases with no result: did a stage give up on them, and why
        why: dict[str, int] = {}
        for c in cases - set(result):
            bad = next((outcome[(s, c)][1] for s in stage_order if outcome.get((s, c), ("",))[0] in ("skipped", "failed")), None)
            if bad is not None:
                w = _reason_word(bad, rules)
                why[w] = why.get(w, 0) + 1
        stages = [{"stage": s, "done": sum(1 for c in cases if (s, c) in outcome)} for s in stage_order]
        part_rows, etas = [], []
        for p in parts:
            state, seen = _part_state(con, p)
            if seen and state == "running" and (not last_at or seen > last_at):
                last_at = seen
            n = len(members[p["batch_id"]])
            got = sum(1 for c in members[p["batch_id"]] if c in finished)
            eta = None
            if state == "running":
                cur = con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status='running' ORDER BY ord LIMIT 1",
                                  (p["batch_id"],)).fetchone()
                if cur:
                    acc = con.execute("SELECT COUNT(DISTINCT case_id) FROM case_event WHERE batch_id=? AND stage=? AND status<>'started'",
                                      (p["batch_id"], cur[0])).fetchone()[0]
                    eta = _eta(con, p["batch_id"], cur[0], n, acc)
                    eta = None if eta == "-" else eta
                    if eta:
                        etas.append(eta)
            dl = con.execute("SELECT version, at, n_rows FROM deliverable WHERE batch_id=? ORDER BY id DESC LIMIT 1", (p["batch_id"],)).fetchone()
            acc = con.execute("SELECT decision, decided_at FROM acceptance WHERE batch_id=? AND decision IS NOT NULL ORDER BY id DESC LIMIT 1",
                              (p["batch_id"],)).fetchone()
            aliases = json.loads(p["aliases"] or "[]")
            part_rows.append({"name": aliases[0] if aliases else p["purpose"][:60], "type": p["type"], "state": state,
                              "pause": _pause_word(p["status_reason"]) if state == "paused" else None,
                              "n": n, "finished": got, "started": p["created"], "ended": p["closed"], "eta": eta,
                              "spent": round(spent.get(p["batch_id"], 0) or 0, 2),
                              "delivered": dict(dl) if dl else None, "accepted": dict(acc) if acc else None})
        states = {r["state"] for r in part_rows}
        if states == {"done"}:
            status = "done"
        elif "running" not in states and cases and len(finished) == len(cases):
            status = "results_in"                # every case has a result, but a batch was stopped and never closed
        else:
            status = next(s for s in ("running", "stopped", "paused", "waiting", "done") if s in states)
        pause = next((r["pause"] for r in part_rows if r["state"] == "paused"), None) if status == "paused" else None
        for c, at in first_final.items():
            if c in cases:
                daily[at[:10]] = daily.get(at[:10], 0) + 1
        open_issues = con.execute(f"SELECT COUNT(*) FROM issue WHERE batch_id IN ({q}) AND status IN ('open','in_progress','fixed')",
                                  ids).fetchone()[0] if con.execute("SELECT 1 FROM sqlite_master WHERE name='issue'").fetchone() else 0
        out.append({"project": project, "scope": scope, "status": status, "pause": pause, "n_cases": len(cases),
                    "finished": len(finished), "with_result": len(result),
                    "good": sum(e["n"] for e in results if e["good"]), "results": results,
                    "why": sorted(({"word": k, "n": n} for k, n in why.items()), key=lambda x: -x["n"]),
                    "stages": stages, "parts": part_rows, "started": min(p["created"] for p in parts),
                    "last_activity": last_at, "eta": etas[0] if len(etas) == 1 else (max(etas, key=_eta_minutes) if etas else None),
                    "today": sum(1 for c, at in first_final.items() if at > day_ago and c in cases),
                    "spent": round(sum(r["spent"] for r in part_rows), 2), "open_issues": open_issues})
    order = {"running": 0, "stopped": 1, "paused": 2, "waiting": 3, "results_in": 4, "done": 5}
    out.sort(key=lambda p: (order[p["status"]], -(dt.datetime.fromisoformat(p["last_activity"] or p["started"]).timestamp())))
    days = [(now.date() - dt.timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    tot = {k: sum(p[k] for p in out) for k in ("n_cases", "finished", "with_result", "good", "today", "spent")}
    tot["packages"] = len(out)
    tot["running"] = sum(1 for p in out if p["status"] == "running")
    return {"at": now.isoformat(timespec="seconds"), "totals": tot, "packages": out, "labels": labels,
            "reasons": reasons_cfg, "daily": [{"day": d, "n": daily.get(d, 0)} for d in days]}


def _eta_minutes(s: str) -> float:
    try:
        v, u = s.split()
        return float(v) * (60 if u == "h" else 1)
    except ValueError:
        return 0
