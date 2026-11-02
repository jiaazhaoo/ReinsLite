"""reins find X -- one lookup over what is already linked (user 2026-11-02: an index linking production, cost, development).

Nothing is stored: every answer is read now from the registry (and, for a case, from the project's own lookups), so it
can never drift from the facts. X may be any id:

  case        the batches that held it (with the version, the result, the cost per case), reviewer and client
              verdicts, the issues on it and the change that fixed them, plus the project's own facts ([find] commands)
  batch       project, type, status, version and module versions per stage, owner and sessions, spend (by stage and
              version, per case), lineage, issues, deliverables, the release's real miss rate
  release     the module versions in it, the batches that ran it, their spend, its real miss rate
  version     what it changed, who made it, the releases that carry it, batches run with it, spend through it, issues
  session     what it opened, developed and spent
  issue       its batch, cases, symptom, the version that fixed it (commit) and whether that was verified
  path        the batch whose work dir or deliverable holds it

[find] in a project's reins.toml:  commands = ["python3 /env/code/ledger/find_case.py {id}"] -- each prints JSON lines
{"section": "...", "label": "...", "value": "..."} that are shown under the case.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import batches


def _one(con, sql, args=()):
    return con.execute(sql, args).fetchone()


def kind_of(con, x: str) -> tuple[str, str]:
    x = x.strip()
    if re.fullmatch(r"#?\d+", x) and _one(con, "SELECT 1 FROM issue WHERE id=?", (int(x.lstrip("#")),)):
        return "issue", x.lstrip("#")
    if _one(con, "SELECT 1 FROM batch WHERE batch_id=?", (x,)):
        return "batch", x
    if _one(con, "SELECT 1 FROM release WHERE name=?", (x,)):
        return "release", x
    if _one(con, "SELECT 1 FROM module_version WHERE version=?", (x,)):
        return "version", x
    s = _one(con, "SELECT id FROM session WHERE id LIKE ?", (x + "%",)) if re.fullmatch(r"[0-9a-f-]{8,}", x) else None
    if s:
        return "session", s[0]
    if x.startswith("/") or x.startswith("s3://"):
        return "path", x
    core = re.sub(r"^[A-Za-z]+_", "", x)                    # SHF_104977 -> 104977 (a project's case prefix)
    for c in (x, core):
        if _one(con, "SELECT 1 FROM batch_case WHERE case_id=? LIMIT 1", (c,)):
            return "case", c
    return "case", core


def _spend(con, where: str, args) -> dict:
    r = _one(con, f"SELECT COALESCE(SUM(amount),0), COUNT(*) FROM spend WHERE {where}", args)
    return {"usd": round(r[0], 2), "calls": r[1]}


def _sessions_of(con, bid: str) -> list[dict]:
    from . import sessions
    try:
        return [{"session": s["session"][:8], "actions": ", ".join(s["actions"][:6]), "transcript": s.get("transcript")}
                for s in sessions.index_for_batch(con, bid)]
    except Exception:                                                           # noqa: BLE001
        return []


def batch(con, bid: str) -> dict:
    b = batches.get(con, bid)
    st = con.execute("SELECT stage, status, module_version FROM batch_stage WHERE batch_id=? ORDER BY ord", (bid,)).fetchall()
    total = _spend(con, "batch_id=?", (bid,))
    by_stage = [dict(r) for r in con.execute("SELECT stage, ROUND(SUM(amount),2) usd FROM spend WHERE batch_id=? GROUP BY 1"
                                             " HAVING usd>0 ORDER BY 2 DESC", (bid,))]
    by_version = [dict(r) for r in con.execute("SELECT COALESCE(module_version,'-') version, ROUND(SUM(amount),2) usd FROM spend"
                                               " WHERE batch_id=? GROUP BY 1 HAVING usd>0 ORDER BY 2 DESC", (bid,))]
    return {"kind": "batch", "id": bid, "project": b["project"], "type": b["type"], "status": b["status"],
            "purpose": b["purpose"], "cases": b["n_cases"], "work_dir": b["work_dir"], "created": b["created"],
            "version": b["release_name"] or b["workflow"], "parent": b["parent"],
            "stages": [dict(r) for r in st], "owner": (b["owner_session"] or "")[:8], "sessions": _sessions_of(con, bid),
            "spend": {**total, "per_case": round(total["usd"] / b["n_cases"], 4) if b["n_cases"] else None,
                      "by_stage": by_stage, "by_version": by_version},
            "issues": [dict(r) for r in con.execute("SELECT id, status, symptom, version FROM issue WHERE batch_id=?", (bid,))],
            "deliverables": [dict(r) for r in con.execute("SELECT path, n_rows, at FROM deliverable WHERE batch_id=?", (bid,))],
            "later_runs": [r[0] for r in con.execute("SELECT batch_id FROM batch WHERE parent=?", (bid,))]}


def release(con, name: str) -> dict:
    r = _one(con, "SELECT * FROM release WHERE name=?", (name,))
    bats = [dict(x) for x in con.execute("SELECT batch_id, type, status, n_cases FROM batch WHERE release_name=? ORDER BY created", (name,))]
    sp = _spend(con, "batch_id IN (SELECT batch_id FROM batch WHERE release_name=?)", (name,))
    q = None
    try:
        from . import quality
        for p in quality.latest(con):
            q = next((x for x in p["releases"] if x["release"] == name), q)
    except Exception:                                                           # noqa: BLE001
        pass
    return {"kind": "release", "id": name, "commit": r["commit_sha"], "created": r["created"], "note": r["note"],
            "versions": json.loads(r["versions"] or "[]") if (r["versions"] or "").startswith("[") else r["versions"],
            "batches": bats, "spend": sp, "quality": q}


def version(con, v: str) -> dict:
    m = _one(con, "SELECT * FROM module_version WHERE version=?", (v,))
    rels = [r[0] for r in con.execute("SELECT name FROM release WHERE versions LIKE ?", (f"%{v}%",))]
    return {"kind": "version", "id": v, "module": m["module"], "status": m["status"], "about": m["about"],
            "commit": m["commit_sha"], "session": (m["session"] or "")[:8], "created": m["created"], "releases": rels,
            "batches": [r[0] for r in con.execute("SELECT DISTINCT batch_id FROM batch_stage WHERE module_version=?", (v,))],
            "spend": _spend(con, "module_version=?", (v,)),
            "issues": [dict(r) for r in con.execute("SELECT id, status, symptom FROM issue WHERE version=?", (v,))]}


def session_(con, sid: str) -> dict:
    s = _one(con, "SELECT * FROM session WHERE id=?", (sid,))
    owned = [r[0] for r in con.execute("SELECT batch_id FROM batch WHERE owner_session=? ORDER BY created DESC", (sid,))]
    return {"kind": "session", "id": sid[:8], "status": s["status"], "purpose": s["purpose"], "role": s["role"],
            "transcript": s["transcript"], "parent": (s["parent"] or "")[:8], "batches": owned,
            "versions": [r[0] for r in con.execute("SELECT version FROM module_version WHERE session=?", (sid,))],
            "spend": _spend(con, "batch_id IN (SELECT batch_id FROM batch WHERE owner_session=?)", (sid,))}


def issue(con, iid: str) -> dict:
    i = _one(con, "SELECT * FROM issue WHERE id=?", (int(iid),))
    fix = _one(con, "SELECT version, status, commit_sha FROM module_version WHERE version=?", (i["version"],)) if i["version"] else None
    return {"kind": "issue", "id": f"#{iid}", "status": i["status"], "symptom": i["symptom"], "batch": i["batch_id"],
            "module": i["module"], "cases": json.loads(i["cases"] or "[]"), "opened": i["at"],
            "fixed_by": dict(fix) if fix else None, "closed": i["closed_at"], "why": i["close_why"]}


def path(con, p: str) -> dict:
    hits = [dict(r) for r in con.execute("SELECT batch_id, path FROM deliverable WHERE ? LIKE path || '%' OR path=?", (p, p))]
    hits += [{"batch_id": r[0], "path": r[1]} for r in con.execute(
        "SELECT batch_id, work_dir FROM batch WHERE work_dir<>'' AND (? LIKE work_dir || '/%' OR ?=work_dir)", (p, p))]
    m = re.search(r"/runs/([a-z0-9-]+-(?:production|rework|experiment|pilot|eval|intake|benchmark_build|smoke|drift|training)-\d{8}-\d+)", p)
    if m and not hits and _one(con, "SELECT 1 FROM batch WHERE batch_id=?", (m.group(1),)):
        hits.append({"batch_id": m.group(1), "path": p})
    return {"kind": "path", "id": p, "batches": hits}


def case(con, c: str) -> dict:
    from . import production
    from .paths import work_dir
    held, other = [], []
    for b in con.execute("SELECT b.* FROM batch_case x JOIN batch b ON b.batch_id=x.batch_id WHERE x.case_id=? ORDER BY b.created", (c,)):
        if b["type"] not in ("production", "rework"):
            other.append(f"{b['type']}:{b['batch_id']}")       # experiments, benchmarks, intake: listed, not detailed
            continue
        spec = production._cfg(b["project"]).get("results") or {}
        wd = work_dir(b)
        res = production._results(Path(wd) / spec["file"], spec).get(c) if spec.get("file") and wd else None
        sp = _spend(con, "batch_id=?", (b["batch_id"],))
        held.append({"batch": b["batch_id"], "type": b["type"], "status": b["status"], "version": b["release_name"] or b["workflow"],
                     "result": " / ".join(x for x in (res or ()) if x) or None,
                     "cost_per_case": round(sp["usd"] / b["n_cases"], 4) if b["n_cases"] else None})
    verdicts, seen = [], set()
    for r in con.execute("SELECT reviewer, verdict, error_type, note, at FROM review_event WHERE case_id=? AND action='verdict' ORDER BY at", (c,)):
        k = (r[0], r[1], r[4])
        if k not in seen:
            seen.add(k)
            verdicts.append({"by": r[0], "verdict": r[1], "type": r[2], "note": (r[3] or "")[:160], "at": r[4]})
    iss = [dict(r) for r in con.execute("SELECT id, status, symptom, version FROM issue WHERE cases LIKE ?", (f'%"{c}"%',))]
    extra = []
    cmds = []
    for b in {r[0] for r in con.execute("SELECT DISTINCT b.project FROM batch_case x JOIN batch b ON b.batch_id=x.batch_id"
                                       " WHERE x.case_id=?", (c,))} or set():
        cmds += (production._cfg(b).get("find") or {}).get("commands", [])
    if not cmds:                                             # a case no batch held: ask every project's lookup
        from . import projects as _projects
        for _, cfg in _projects.all_():
            cmds += (cfg.get("find") or {}).get("commands", [])
    for cmd in dict.fromkeys(cmds):
        try:
            out = subprocess.run(cmd.replace("{id}", c), shell=True, capture_output=True, text=True, timeout=60).stdout
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in out.splitlines():
            try:
                extra.append(json.loads(line))
            except ValueError:
                pass
    return {"kind": "case", "id": c, "batches": held, "other_runs": other, "verdicts": verdicts[-20:], "issues": iss,
            "facts": extra}


def find(con, x: str) -> dict:
    k, v = kind_of(con, x)
    return {"batch": batch, "release": release, "version": version, "session": session_, "issue": issue,
            "path": path, "case": case}[k](con, v)


def text(d: dict) -> str:
    """A readable chain for the terminal (the board draws the same dict)."""
    L = [f"{d['kind']} {d['id']}"]
    def kv(label, value):
        if value not in (None, "", [], {}):
            L.append(f"  {label:<12} {value}")
    if d["kind"] == "case":
        for f in d["facts"]:
            kv(f.get("label") or f.get("section", ""), f.get("value"))
        for h in d["batches"]:
            kv("run", f"{h['batch']} · {h['version'] or '-'} · {h['result'] or 'no result'} · ${h['cost_per_case'] or 0}/case")
        if d.get("other_runs"):
            kv("also in", f"{len(d['other_runs'])} development runs (" + ", ".join(x.split(':')[0] for x in d["other_runs"][:3]) + " ...)")
        for v in d["verdicts"]:
            kv("verdict", f"{v['by']}: {v['verdict']}{' ' + v['type'] if v['type'] else ''} ({(v['at'] or '')[:10]}) {v['note']}")
        for i in d["issues"]:
            kv("issue", f"#{i['id']} {i['status']} · {i['symptom'][:90]}" + (f" · fixed by {i['version']}" if i["version"] else ""))
    elif d["kind"] == "batch":
        kv("what", f"{d['type']} · {d['status']} · {d['cases']} cases · {d['purpose'][:90]}")
        kv("version", d["version"])
        kv("stages", ", ".join(f"{s['stage']}={s['status']}" + (f" ({s['module_version']})" if s["module_version"] else "") for s in d["stages"]))
        sp = d["spend"]
        kv("spend", f"${sp['usd']} · {sp['calls']} calls · ${sp['per_case'] or 0}/case")
        kv("by stage", ", ".join(f"{x['stage']} ${x['usd']}" for x in sp["by_stage"]))
        kv("by version", ", ".join(f"{x['version']} ${x['usd']}" for x in sp["by_version"]))
        kv("owner", d["owner"])
        main = [s for s in d["sessions"] if "owner" in s["actions"] or "opened" in s["actions"] or "process" in s["actions"]]
        for s in (main or d["sessions"])[:3]:
            kv("session", f"{s['session']} · {s['actions']} · {s['transcript'] or ''}")
        if len(d["sessions"]) > len((main or d["sessions"])[:3]):
            kv("", f"+{len(d['sessions']) - len((main or d['sessions'])[:3])} more sessions touched it (--json)")
        for i in d["issues"]:
            kv("issue", f"#{i['id']} {i['status']} · {i['symptom'][:90]}")
        for x in d["deliverables"]:
            kv("delivered", f"{x['path']} ({x['n_rows']} rows, {x['at'][:10]})")
        kv("work dir", d["work_dir"]); kv("parent", d["parent"]); kv("later runs", ", ".join(d["later_runs"]))
    elif d["kind"] == "release":
        kv("commit", d["commit"]); kv("versions", d["versions"]); kv("spend", f"${d['spend']['usd']}")
        for b in d["batches"]:
            kv("batch", f"{b['batch_id']} · {b['type']} · {b['status']} · {b['n_cases']} cases")
        q = d.get("quality")
        if q:
            kv("quality", f"miss rate {q['miss_rate']} ({q['wrong_auto']}/{q['checked_auto']}), auto {q['auto_share']}")
    elif d["kind"] == "version":
        kv("what", f"{d['module']} · {d['status']} · {d['about'][:100]}"); kv("commit", d["commit"]); kv("by", d["session"])
        kv("releases", ", ".join(d["releases"])); kv("batches", ", ".join(d["batches"][:10])); kv("spend", f"${d['spend']['usd']}")
        for i in d["issues"]:
            kv("issue", f"#{i['id']} {i['status']} · {i['symptom'][:90]}")
    elif d["kind"] == "session":
        kv("what", f"{d['role'] or '-'} · {d['status']} · {d['purpose'] or ''}"); kv("parent", d["parent"]); kv("transcript", d["transcript"])
        kv("batches", ", ".join(d["batches"][:15])); kv("versions", ", ".join(d["versions"][:15])); kv("spend", f"${d['spend']['usd']}")
    elif d["kind"] == "issue":
        kv("what", f"{d['status']} · {d['symptom']}"); kv("batch", d["batch"]); kv("module", d["module"])
        kv("cases", ", ".join(d["cases"][:20])); kv("fixed by", d["fixed_by"]); kv("closed", f"{d['closed'] or ''} {d['why'] or ''}".strip())
    elif d["kind"] == "path":
        for h in d["batches"]:
            kv("batch", f"{h['batch_id']} ({h['path']})")
        if not d["batches"]:
            L.append("  no batch holds this path")
    return "\n".join(L)
