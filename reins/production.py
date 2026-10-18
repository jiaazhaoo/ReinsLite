"""The production page (board /production): what a manager looks at. Production and rework batches only, one block per
work package (batch scope): how many cases have a result, what the results are, why the others could not be processed,
what it cost, how long it took (running vs waiting) and when it should be done. No commands, sessions, versions or
paths. Cost = the gateway ledger plus history imported from before the gateway (reins spend import) whose label is the
batch id or the end of its work dir; time = the batch's processes (or stages run under reins), parallel parts counted
once. Batches adopted from before reins have no times: they are counted and shown as such.

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


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds")


def _union_h(intervals) -> float:
    """Hours covered by at least one interval (parallel parts are not counted twice)."""
    pts = sorted((dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)) for a, b in intervals if a and b and b > a)
    total, cur = 0.0, None
    for a, b in pts:
        if cur and a <= cur[1]:
            cur[1] = max(cur[1], b)
            continue
        if cur:
            total += (cur[1] - cur[0]).total_seconds()
        cur = [a, b]
    if cur:
        total += (cur[1] - cur[0]).total_seconds()
    return total / 3600


def _intersect(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The parts of intervals a that fall inside intervals b (a stage's span while something of its batch ran)."""
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            if not (s1 and e1 and s2 and e2):
                continue
            lo, hi = max(s1, s2), min(e1, e2)
            if hi > lo:
                out.append((lo, hi))
    return out


def _run_intervals(con, bid: str, now: str) -> list[tuple[str, str]]:
    """When the batch had something running: its processes (start to end; a live one to now; a lost one to its last
    outcome), else its stages run under reins. Stages adopted from before reins have no times: empty."""
    from . import runner
    out = []
    for p in con.execute("SELECT * FROM process WHERE batch_id=? ORDER BY id", (bid,)).fetchall():
        end = p["ended"]
        if not end:
            if p["state"] == "running" and (runner._alive(p["pgid"]) or runner._alive(p["supervisor_pid"])):
                end = now
            else:
                end = con.execute("SELECT MAX(at) FROM case_event WHERE batch_id=? AND at>=?", (bid, p["started"])).fetchone()[0]
        out.append((p["started"], end))
    if not out:
        out = [(a, b) for _, a, b in _stage_intervals(con, bid, now)]
    return out


def _stage_intervals(con, bid: str, now: str) -> list[tuple[str, str, str]]:
    out = []
    for r in con.execute("SELECT stage, started, ended, status FROM batch_stage WHERE batch_id=? AND started IS NOT NULL"
                         " AND COALESCE(note,'')<>'adopted'", (bid,)).fetchall():
        end = r["ended"] or con.execute("SELECT MAX(at) FROM case_event WHERE batch_id=? AND stage=?", (bid, r["stage"])).fetchone()[0]
        out.append((r["stage"], r["started"], end or r["started"]))
    return out


def _imported_spend(con, rows) -> dict[str, list[tuple[str, str, float]]]:
    """History imported from before the gateway (reins spend import), by batch: its label is the batch id or the end of
    the batch's work dir (e.g. 'sheffield/wp3-batch2-e2e'). -> {batch_id: [(purpose, day, usd)]}."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='spend_import'").fetchone():
        return {}
    by_label = {}
    for b in rows:
        by_label[b["batch_id"]] = b["batch_id"]
        if b["work_dir"]:
            wd = b["work_dir"].rstrip("/")
            by_label.setdefault(wd, b["batch_id"])
            parts = wd.split("/")
            for i in range(1, len(parts)):
                by_label.setdefault("/".join(parts[i:]), b["batch_id"])
    out: dict[str, list] = {}
    for label, purpose, day, usd in con.execute("SELECT batch_label, purpose, day, SUM(amount) FROM spend_import"
                                                " WHERE batch_label IS NOT NULL GROUP BY 1,2,3"):
        bid = by_label.get(label.strip().rstrip("/"))
        if bid:
            out.setdefault(bid, []).append((purpose, day, usd or 0))
    return out


def view(con) -> dict:
    from .board import _eta
    all_prod = con.execute("SELECT * FROM batch WHERE type IN (%s) ORDER BY created" % ",".join("?" * len(TYPES)), TYPES).fetchall()
    rows = [b for b in all_prod if b["status"] != "failed" and not _abandoned(con, b["batch_id"])]   # failed = superseded or never ran
    dropped = [b for b in all_prod if b not in rows]
    ids_all = {b["batch_id"] for b in all_prod}
    ledger: dict[str, list] = {}                                                   # batch -> [(stage, day, usd)]
    for bid, stage, day, usd in con.execute("SELECT batch_id, stage, substr(at,1,10), SUM(amount) FROM spend GROUP BY 1,2,3"):
        if bid in ids_all:
            ledger.setdefault(bid, []).append((stage, day, usd or 0))
    first_spend = {r[0]: r[1] for r in con.execute("SELECT batch_id, MIN(at) FROM spend GROUP BY batch_id")}
    imported = _imported_spend(con, all_prod)
    ai_all = (con.execute("SELECT COALESCE(SUM(amount),0) FROM spend").fetchone()[0]
              + (con.execute("SELECT COALESCE(SUM(amount),0) FROM spend_import").fetchone()[0]
                 if con.execute("SELECT 1 FROM sqlite_master WHERE name='spend_import'").fetchone() else 0))
    spend_daily: dict[str, float] = {}
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
        now_iso = _iso(now)
        stage_names = set(stage_order)
        cost_stage: dict[str, float] = {}
        stage_iv: dict[str, list] = {}
        run_iv, unknown_time, first_seen = [], 0, []
        part_rows, etas = [], []
        for p in parts:
            pid = p["batch_id"]
            p_spent = 0.0
            for stage, day, usd in ledger.get(pid, []):
                p_spent += usd
                cost_stage[stage] = cost_stage.get(stage, 0) + usd
                spend_daily[day] = spend_daily.get(day, 0) + usd
            if pid in first_spend:
                first_seen.append(first_spend[pid])
            for purpose, day, usd in imported.get(pid, []):
                p_spent += usd
                st = (purpose or "").split(":")[0].strip()
                st = st if st in stage_names else "other"
                cost_stage[st] = cost_stage.get(st, 0) + usd
                if day:
                    spend_daily[day] = spend_daily.get(day, 0) + usd
                    first_seen.append(day + "T00:00:00")
            iv = _run_intervals(con, pid, now_iso)
            if not iv:
                unknown_time += 1
            run_iv += iv
            first_seen += [a for a, _ in iv]
            for st, a, b in _stage_intervals(con, pid, now_iso):         # paused or waiting inside a stage is not its time
                stage_iv.setdefault(st, []).extend(_intersect([(a, b)], iv) if iv else [])
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
                              "spent": round(p_spent, 2), "budget": p["spend_cap"] or 0,
                              "run_h": round(_union_h(iv), 1) if iv else None,
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
        start = min([p["created"] for p in parts] + first_seen)
        ends = [b for _, b in run_iv if b] + [p["closed"] for p in parts if p["closed"]] + ([last_at] if last_at else [])
        end = now_iso if status in ("running", "stopped", "paused", "waiting") else max(ends + [start])
        cal_h = (dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds() / 3600
        run_h = _union_h(run_iv)
        spent_pkg = sum(r["spent"] for r in part_rows)
        time_cost = {"start": start, "end": end, "calendar_h": round(cal_h, 1), "run_h": round(run_h, 1),
                     "wait_h": round(max(0.0, cal_h - run_h), 1) if not unknown_time else None,
                     "unknown_parts": unknown_time, "budget": round(sum(r["budget"] for r in part_rows), 2),
                     "per_case": round(spent_pkg / len(cases), 4) if cases else None,
                     "per_result": round(spent_pkg / len(result), 4) if result else None,
                     "cost_by_stage": [{"stage": k, "usd": round(v, 2)} for k, v in
                                       sorted(cost_stage.items(), key=lambda kv: (stage_order + ["other"]).index(kv[0])
                                              if kv[0] in stage_order + ["other"] else 99) if v > 0.005],
                     "time_by_stage": [{"stage": k, "h": round(_union_h(stage_iv[k]), 1)} for k in stage_order
                                       if k in stage_iv and _union_h(stage_iv[k]) >= 0.05]}
        retry = 0.0                              # money spent on tries of this package that were superseded
        for d in dropped:
            if (d["project"], d["scope"]) == (project, scope):
                for stage, day, usd in ledger.get(d["batch_id"], []) + [(pp, dd, uu) for pp, dd, uu in imported.get(d["batch_id"], [])]:
                    retry += usd
                    if day:
                        spend_daily[day] = spend_daily.get(day, 0) + usd
        spent_pkg += retry
        time_cost["spent_retry"] = round(retry, 2)
        time_cost["per_case"] = round(spent_pkg / len(cases), 4) if cases else None
        time_cost["per_result"] = round(spent_pkg / len(result), 4) if result else None
        open_issues = con.execute(f"SELECT COUNT(*) FROM issue WHERE batch_id IN ({q}) AND status IN ('open','in_progress','fixed')",
                                  ids).fetchone()[0] if con.execute("SELECT 1 FROM sqlite_master WHERE name='issue'").fetchone() else 0
        out.append({"project": project, "scope": scope, "status": status, "pause": pause, "n_cases": len(cases),
                    "finished": len(finished), "with_result": len(result),
                    "good": sum(e["n"] for e in results if e["good"]), "results": results,
                    "why": sorted(({"word": k, "n": n} for k, n in why.items()), key=lambda x: -x["n"]),
                    "stages": stages, "parts": part_rows, "started": min(p["created"] for p in parts),
                    "last_activity": last_at, "eta": etas[0] if len(etas) == 1 else (max(etas, key=_eta_minutes) if etas else None),
                    "today": sum(1 for c, at in first_final.items() if at > day_ago and c in cases),
                    "spent": round(spent_pkg, 2), "open_issues": open_issues, **time_cost})
    order = {"running": 0, "stopped": 1, "paused": 2, "waiting": 3, "results_in": 4, "done": 5}
    out.sort(key=lambda p: (order[p["status"]], -(dt.datetime.fromisoformat(p["last_activity"] or p["started"]).timestamp())))
    days = [(now.date() - dt.timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    tot = {k: sum(p[k] for p in out) for k in ("n_cases", "finished", "with_result", "good", "today", "spent")}
    tot["packages"] = len(out)
    tot["spent"] = round(tot["spent"], 2)
    tot["ai_all"] = round(ai_all, 2)
    tot["per_case"] = round(tot["spent"] / tot["n_cases"], 4) if tot["n_cases"] else None
    tot["per_result"] = round(tot["spent"] / tot["with_result"], 4) if tot["with_result"] else None
    tot["run_h"] = round(sum(p["run_h"] for p in out), 1)
    tot["unknown_parts"] = sum(p["unknown_parts"] for p in out)
    week = {(now.date() - dt.timedelta(days=i)).isoformat() for i in range(7)}
    tot["spent_7d"] = round(sum(v for d, v in spend_daily.items() if d in week), 2)
    tot["running"] = sum(1 for p in out if p["status"] == "running")
    dev = development(con, ids_all, imported, days)
    table = batch_table(con, rows, ledger, imported)
    return {"at": now.isoformat(timespec="seconds"), "totals": tot, "dev": dev, "batches": table, "packages": out, "labels": labels,
            "reasons": reasons_cfg, "daily": [{"day": d, "n": daily.get(d, 0), "usd": round(spend_daily.get(d, 0), 2)} for d in days]}


def _eta_minutes(s: str) -> float:
    try:
        v, u = s.split()
        return float(v) * (60 if u == "h" else 1)
    except ValueError:
        return 0


def development(con, prod_ids: set, prod_imported: dict, days: list[str]) -> dict:
    """What development cost: every AI call not made for a production or rework batch (experiments, evaluations,
    pilots, drift checks, imported history from before reins that no production batch claims), by kind, by module and
    by change; and how long changes took from start to release."""
    now = dt.datetime.now()
    claimed = {(p, d, round(u, 6)) for v in prod_imported.values() for p, d, u in v}
    by_kind: dict[str, float] = {}
    by_module: dict[str, float] = {}
    by_version: dict[str, float] = {}
    daily: dict[str, float] = {}
    stage_mv = {}
    for bid, mv in con.execute("SELECT batch_id, MIN(module_version) FROM batch_stage WHERE module_version IS NOT NULL"
                               " GROUP BY batch_id HAVING COUNT(DISTINCT module_version)=1"):
        stage_mv[bid] = mv
    btype = {r[0]: r[1] for r in con.execute("SELECT batch_id, type FROM batch")}
    mod_of = {r[0]: r[1] for r in con.execute("SELECT version, module FROM module_version")}
    for bid, mv, day, usd in con.execute("SELECT batch_id, module_version, substr(at,1,10), SUM(amount) FROM spend GROUP BY 1,2,3"):
        if bid in prod_ids or not usd:
            continue
        kind = btype.get(bid, "other")
        by_kind[kind] = by_kind.get(kind, 0) + usd
        v = mv or stage_mv.get(bid)
        if v:
            by_version[v] = by_version.get(v, 0) + usd
        m = mod_of.get(v, "") if v else ""
        by_module[m or "unassigned"] = by_module.get(m or "unassigned", 0) + usd
        daily[day] = daily.get(day, 0) + usd
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='spend_import'").fetchone():
        for label, purpose, day, usd in con.execute("SELECT batch_label, purpose, day, SUM(amount) FROM spend_import GROUP BY 1,2,3"):
            if (purpose, day, round(usd or 0, 6)) in claimed or not usd:
                continue
            by_kind["history"] = by_kind.get("history", 0) + usd
            by_module["unassigned"] = by_module.get("unassigned", 0) + usd
            if day:
                daily[day] = daily.get(day, 0) + usd
    ev: dict[str, dict] = {}
    for v, event, at in con.execute("SELECT version, event, at FROM module_event WHERE event IN ('created','released','abandoned') ORDER BY id"):
        ev.setdefault(v, {}).setdefault(event, at)
    changes = []
    for r in con.execute("SELECT v.*, m.project FROM module_version v JOIN module m ON m.name=v.module"
                         " WHERE v.suffix<>'main' ORDER BY v.created DESC"):          # <module>-main-*: the baseline registered at adoption
        e = ev.get(r["version"], {})
        start = e.get("created") or r["created"]
        end = e.get("released") or e.get("abandoned")
        h = ((dt.datetime.fromisoformat(end) if end else now) - dt.datetime.fromisoformat(start)).total_seconds() / 3600
        changes.append({"project": r["project"], "module": r["module"], "about": r["about"], "status": r["status"],
                        "start": start, "end": end, "hours": round(h, 1), "spent": round(by_version.get(r["version"], 0), 2)})
    recent = (now - dt.timedelta(days=14)).isoformat(timespec="seconds")
    released_14 = [c for c in changes if c["status"] in ("released", "retired") and c["end"] and c["end"] >= recent]
    rel_all = [c for c in changes if c["status"] in ("released", "retired") and c["end"]]
    total = sum(by_kind.values())
    week = {(now.date() - dt.timedelta(days=i)).isoformat() for i in range(7)}
    hrs = sorted(c["hours"] for c in rel_all)
    return {"spent": round(total, 2), "spent_7d": round(sum(v for d, v in daily.items() if d in week), 2),
            "by_kind": sorted(({"kind": k, "usd": round(v, 2)} for k, v in by_kind.items() if v >= 0.005), key=lambda x: -x["usd"]),
            "by_module": sorted(({"module": k, "usd": round(v, 2)} for k, v in by_module.items() if v >= 0.005), key=lambda x: -x["usd"]),
            "daily": [{"day": d, "usd": round(daily.get(d, 0), 2)} for d in days],
            "released_14d": len(released_14), "in_progress": sum(1 for c in changes if c["status"] == "candidate"),
            "median_h": hrs[len(hrs) // 2] if hrs else None, "max_h": hrs[-1] if hrs else None,
            "changes": [c for c in changes if c["status"] == "candidate" or c in released_14 or c["spent"] > 0][:40]}


def batch_table(con, rows, ledger: dict, imported: dict) -> list[dict]:
    """One row per production / rework batch, for comparing batches of one project: its own cases, its own result rows
    (a results file shared by several batches belongs to the newest; the older ones say so), reviewers' verdicts,
    spend and running time. Columns a project does not have are absent, not zero."""
    now_iso = _iso(dt.datetime.now())
    by_project: dict[str, list] = {}
    for b in rows:
        by_project.setdefault(b["project"], []).append(b)
    out = []
    for project, bs in by_project.items():
        cfg = _cfg(project)
        spec = cfg.get("results") or {}
        scope_names = (cfg.get("batch") or {}).get("scope") or ["scope"]
        good_vals = set(spec.get("good") or [])
        owner: dict[str, str] = {}                          # results file -> newest batch using it
        for b in sorted(bs, key=lambda b: b["created"]):
            if spec.get("file") and b["work_dir"]:
                owner[str(Path(b["work_dir"]) / spec["file"])] = b["batch_id"]
        values, details, table = {}, {}, []
        for b in sorted(bs, key=lambda b: b["created"]):
            bid = b["batch_id"]
            cases = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (bid,))}
            row = {"batch_id": bid, "name": (json.loads(b["aliases"] or "[]") or [b["purpose"][:60]])[0], "type": b["type"],
                   "status": b["status"], "date": (b["created"] or "")[:10], "closed": (b["closed"] or "")[:10],
                   "scope": dict(zip(scope_names, _split_scope(b["scope"], len(scope_names)))), "n": len(cases)}
            f = str(Path(b["work_dir"]) / spec["file"]) if spec.get("file") and b["work_dir"] else None
            if f and owner.get(f) != bid:
                row["results_in"] = next((x["name"] for x in table if x["batch_id"] == owner[f]), None) or \
                    (json.loads(next(x for x in bs if x["batch_id"] == owner[f])["aliases"] or "[]") or [owner[f]])[0]
            res = {} if not f or "results_in" in row else {c: v for c, v in _results(Path(f), spec).items() if c in cases}
            if res:
                vc, dc = {}, {}
                for v, d in res.values():
                    vc[v] = vc.get(v, 0) + 1
                    values[v] = values.get(v, 0) + 1
                    if d and v not in good_vals:
                        dc[d] = dc.get(d, 0) + 1
                        details[d] = details.get(d, 0) + 1
                row.update({"with_result": len(res), "values": vc, "details": dc})
            # reviewers: the latest verdict per (case, reviewer); a case two reviewers judged is consistent or not
            verd: dict[tuple, str] = {}
            last_review = None
            for case, rev, v, at in con.execute("SELECT case_id, reviewer, verdict, at FROM review_event WHERE batch_id=?"
                                                " AND action='verdict' ORDER BY id", (bid,)):
                verd[(case, rev)] = v
                last_review = at
            if verd:
                per_case: dict[str, list] = {}
                for (case, _), v in verd.items():
                    per_case.setdefault(case, []).append(v)
                final = {c: vs[-1] for c, vs in per_case.items()}
                two = {c: vs for c, vs in per_case.items() if len(vs) > 1}
                good_reviewed = [c for c in final if res.get(c, ("",))[0] in good_vals]
                row["review"] = {"date": (last_review or "")[:10], "n": len(final),
                                 "correct": sum(1 for v in final.values() if v == "correct"),
                                 "wrong": sum(1 for v in final.values() if v == "wrong"),
                                 "unsure": sum(1 for v in final.values() if v == "unsure"),
                                 "double": len(two), "agree": sum(1 for vs in two.values() if len(set(vs)) == 1),
                                 "good_reviewed": len(good_reviewed),
                                 "good_wrong": sum(1 for c in good_reviewed if final[c] == "wrong")}
            spent = sum(u for _, _, u in ledger.get(bid, [])) + sum(u for _, _, u in imported.get(bid, []))
            iv = _run_intervals(con, bid, now_iso)
            row.update({"spent": round(spent, 2), "per_case": round(spent / len(cases), 4) if cases else None,
                        "run_h": round(_union_h(iv), 1) if iv else None,
                        "note": b["status_reason"] if b["status"] == "paused" else None,
                        "pause": _pause_word(b["status_reason"]) if b["status"] == "paused" else None})
            table.append(row)
        order = list(spec.get("good") or []) + [v for v in sorted(values, key=lambda v: -values[v]) if v not in good_vals]
        out.append({"project": project, "scope_names": scope_names, "good": sorted(good_vals),
                    "values": [v for v in order if v in values], "details": sorted(details),
                    "rows": table})
    return out


def _split_scope(scope: str, n: int) -> list[str]:
    """'mansfield-low' with scope parts [council, wp] -> ['mansfield', 'low']; extra dashes stay in the last part."""
    parts = scope.split("-", n - 1) if n > 1 else [scope]
    return parts + [""] * (n - len(parts))
