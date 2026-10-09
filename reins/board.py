"""C14 board: a read-only web app over the registry (front end in board_ui/: index.html, app.css, app.js).

    概览 overview   alerts, four numbers (running, developing, spent today, lowest balance), the pipeline, runs, changes
    流程图谱 pipeline  the newest frozen workflow: each stage's code version and its versioned tools (stage drawer)
    运行 runs       running batches with a stage stepper, finished batches
    开发 dev        changes in progress, released versions and what each did
    成本 cost       budget pool, provider balances, spend by batch
    工具箱 toolbox  every prompt / model / weights / tool the workflow uses, by shelf, searchable

    reins board serve [--port 8791]        read-only, refreshes every 30 s, data from the registry only
No session ids, paths or timelines are shown (user, 2026-10-07): sessions are only the index behind these blocks.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import batches, gate, issues, notify, sessions, spend
from .store import config, connect

_PROBE: dict[str, tuple[float, list[str]]] = {}


def probe(b) -> list[str]:
    """Output of the batch's probe command, cached 60 s (the board refreshes every 30 s)."""
    cmd = b["probe_cmd"] if "probe_cmd" in b.keys() else None
    if not cmd:
        return []
    hit = _PROBE.get(b["batch_id"])
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        r = subprocess.run(cmd.format(work_dir=b["work_dir"] or ""), shell=True, capture_output=True, text=True, timeout=20)
        lines = [l for l in (r.stdout or r.stderr).splitlines() if l.strip()][:30]
    except subprocess.TimeoutExpired:
        lines = ["(progress probe timed out)"]
    _PROBE[b["batch_id"]] = (time.time(), lines)
    return lines


def _age(ts: str | None) -> str:
    if not ts:
        return "-"
    m = (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() / 60
    return f"{m / 60:.1f} h" if m >= 90 else f"{m:.0f} min"


def _eta(con, bid: str, stage: str, n_cases: int, done: int) -> str:
    if done == 0:
        return "-"
    rows = con.execute("SELECT at FROM case_event WHERE batch_id=? AND stage=? AND status IN ('done','skipped','failed')"
                       " AND at > ? ORDER BY id", (bid, stage, (dt.datetime.now() - dt.timedelta(minutes=30)).isoformat(timespec="seconds"))).fetchall()
    if len(rows) < 2:
        return "-"
    span = (dt.datetime.fromisoformat(rows[-1][0]) - dt.datetime.fromisoformat(rows[0][0])).total_seconds() / 60
    rate = len(rows) / span if span > 0 else 0
    if rate <= 0:
        return "-"
    left = (n_cases - done) / rate
    return f"{left / 60:.1f} h" if left > 90 else f"{left:.0f} min"


def _latest_notes(con, hours: int = 6) -> list[dict]:
    """One line per notification key (the newest), from the last few hours; stale stage notices dropped."""
    since = (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = con.execute("SELECT * FROM notification WHERE acked=0 AND at>? AND id IN (SELECT MAX(id) FROM notification"
                       " GROUP BY key) ORDER BY id DESC LIMIT 8", (since,)).fetchall()
    out = []
    for r in rows:
        k = r["key"].split(":")
        if k[0] in ("stall", "late") and len(k) == 3:
            st = con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage=?", (k[1], k[2])).fetchone()
            if not st or st["status"] != "running":
                continue
        if r["level"] != "info":
            out.append(dict(r))
    return out


def running_cards(con) -> list[dict]:
    out = []
    for b in batches.live(con):
        st = batches.status(con, b["batch_id"])
        cur = next((s for s in st["stages"] if s["status"] == "running"), None) or \
              next((s for s in st["stages"] if s["status"] == "planned"), None)
        needs = []
        if any(s["paid"] for s in st["stages"]) and b["spend_cap"] <= 0:
            needs.append(f"等批花费：reins batch approve {b['batch_id']} --cap USD")
        orphan = con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='outcome_without_input' ORDER BY id DESC LIMIT 1",
                             (b["batch_id"],)).fetchone()
        if orphan:
            needs.append(f"有结果但缺输入：{orphan[0]}")
        acc = (cur["done"] + cur["skipped"] + cur["failed"]) if cur else 0
        adopted = bool(cur and cur["note"] == "adopted" and cur["done"] == 0)
        health = "red" if b["status"] == "paused" or needs else "green"
        if cur and cur["failed"] > 0.05 * max(1, acc):
            health = "yellow" if health == "green" else health
        skipped = con.execute("SELECT COUNT(DISTINCT case_id) FROM case_current WHERE batch_id=? AND status='skipped'", (b["batch_id"],)).fetchone()[0]
        reasons = [f"{r[1]} × {r[0]}" for r in con.execute(
            "SELECT reason, COUNT(*) FROM case_current WHERE batch_id=? AND status='skipped' GROUP BY reason ORDER BY 2 DESC", (b["batch_id"],))]
        aliases = json.loads(b["aliases"])
        iss = [{"id": i["id"], "status": i["status"], "symptom": i["symptom"], "n": len(i["cases"]), "version": i["version"]}
               for i in issues.list_(con, False, b["batch_id"])]
        out.append({"batch_id": b["batch_id"], "title": aliases[0] if aliases else b["purpose"], "purpose": b["purpose"],
                    "type": b["type"], "status": b["status"], "status_reason": b["status_reason"], "issues": iss,
                    "n_cases": b["n_cases"], "n_skipped": skipped, "skip_reasons": reasons,
                    "stage": cur["stage"] if cur else "-", "stage_ord": f"{cur['ord'] + 1}/{len(st['stages'])}" if cur else "-",
                    "stage_age": _age(cur["started"]) if cur else "-",
                    "progress": "账本未接入（旧版本启动）" if adopted else f"{acc}/{b['n_cases']}",
                    "pct": round(100 * acc / max(1, b["n_cases"])), "adopted": adopted,
                    "eta": _eta(con, b["batch_id"], cur["stage"], b["n_cases"], acc) if cur else "-",
                    "health": health, "needs": needs, "spent": st["batch"]["spent"], "cap": b["spend_cap"],
                    "time_used_h": round(batches.elapsed_h(con, b["batch_id"]) or 0, 1), "time_budget_h": b["time_budget_h"],
                    "release": b["release_name"], "mixed": bool(b["mixed_version"]), "probe": probe(b),
                    "aliases": aliases[1:],
                    "stages": [{"stage": x["stage"], "status": x["status"], "paid": bool(x["paid"]),
                                "done": x["done"] + x["skipped"] + x["failed"], "failed": x["failed"],
                                "module_version": x["module_version"]} for x in st["stages"]]})
    return out


def in_progress(con) -> list[dict]:
    out = []
    for d in sessions.development(con):
        g = gate.latest(con, d["version"]) if d["version"] else None
        idle_h = round((dt.datetime.now() - dt.datetime.fromisoformat(d["last"])).total_seconds() / 3600, 1) if d["last"] else None
        files = con.execute("SELECT COUNT(DISTINCT path) FROM session_event WHERE kind='edit' AND module=? AND at>?",
                            (d["module"], (dt.datetime.now() - dt.timedelta(hours=48)).isoformat(timespec="seconds"))).fetchone()[0]
        from_batch = issue = None
        if d["version"]:
            pins = con.execute("SELECT pins FROM module_version WHERE version=?", (d["version"],)).fetchone()
            pv = json.loads(pins[0]) if pins else {}
            from_batch, issue = pv.get("from_batch"), pv.get("issue")
        out.append({**d, "files": files, "from_batch": from_batch, "issue": issue,
                    "gate": (f"{'通过' if g['status'] == 'green' else '未通过'}：漏放 {g['missed_error']}/{g['base_missed_error']}，"
                             f"审核量 {g['review_load']}/{g['base_review_load']}") if g else "还没过门禁",
                    "gate_status": g["status"] if g else "none", "idle_h": idle_h, "stalled": bool(idle_h and idle_h > 24)})
    return out


def dev_history(con, limit: int = 20) -> list[dict]:
    rows = con.execute(
        "SELECT v.version, v.module, v.about, v.pins, v.status, v.created,"
        " (SELECT at FROM module_event e WHERE e.version=v.version AND e.event='released' ORDER BY id DESC LIMIT 1) AS released_at,"
        " (SELECT detail FROM module_event e WHERE e.version=v.version AND e.event='released' ORDER BY id DESC LIMIT 1) AS evidence,"
        " (SELECT detail FROM module_event e WHERE e.version=v.version AND e.event IN ('retired','abandoned') ORDER BY id DESC LIMIT 1) AS why_out"
        " FROM module_version v WHERE v.status IN ('released','retired','abandoned')"
        " ORDER BY COALESCE(released_at, v.created) DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        g = gate.latest(con, r["version"])
        pins = json.loads(r["pins"] or "{}")
        out.append({"version": r["version"], "module": r["module"], "about": r["about"], "status": r["status"],
                    "released_at": r["released_at"] or r["created"], "from_batch": pins.get("from_batch"), "why_out": r["why_out"],
                    "artifacts": pins.get("artifacts", []),
                    "gate": (f"漏放 {g['missed_error']}/{g['base_missed_error']}，审核量 {g['review_load']}/{g['base_review_load']}"
                             f"（{g['benchmark']}）") if g else (r["evidence"] or "")[:120]})
    return out


def run_history(con, limit: int = 15) -> list[dict]:
    out = []
    for b in con.execute("SELECT * FROM batch WHERE status IN ('closed','done','failed') ORDER BY created DESC LIMIT ?", (limit,)):
        bid = b["batch_id"]
        stages = con.execute("SELECT stage, status, started, ended FROM batch_stage WHERE batch_id=? ORDER BY ord", (bid,)).fetchall()
        last = con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status='done' ORDER BY ord DESC LIMIT 1", (bid,)).fetchone()
        led = batches.ledger(con, bid, last["stage"]) if last else None
        acc = con.execute("SELECT decision, n FROM acceptance WHERE batch_id=? ORDER BY id DESC LIMIT 1", (bid,)).fetchone()
        starts = [s["started"] for s in stages if s["started"]]
        ends = [s["ended"] for s in stages if s["ended"]]
        hours = round((dt.datetime.fromisoformat(max(ends)) - dt.datetime.fromisoformat(min(starts))).total_seconds() / 3600, 1) \
            if starts and ends else None
        aliases = json.loads(b["aliases"])
        out.append({"batch_id": bid, "title": aliases[0] if aliases else b["purpose"], "purpose": b["purpose"], "type": b["type"],
                    "status": b["status"], "n_cases": b["n_cases"], "release": b["release_name"],
                    "stages_done": sum(1 for s in stages if s["status"] in ("done", "skipped")), "stages": len(stages),
                    "done": led["done"] if led else None, "skipped": led["skipped"] if led else None, "failed": led["failed"] if led else None,
                    "spent": round(batches.spent(con, bid), 2), "hours": hours, "closed": b["closed"] or b["created"],
                    "decision": acc["decision"] if acc else None, "mixed": bool(b["mixed_version"])})
    return out


_GIT: dict[str, tuple[float, list[dict]]] = {}


def unregistered_dev(con) -> list[dict]:
    """Development reins does not know about: branches in a managed project that are ahead of main and are not the
    branch of a registered module version (work started before reins, or around it). Cached 60 s."""
    from . import projects
    known = {r[0] for r in con.execute("SELECT branch FROM module_version WHERE branch IS NOT NULL")}
    out = []
    for repo, cfg in projects.all_():
        key = str(repo)
        hit = _GIT.get(key)
        if hit and time.time() - hit[0] < 60:
            out += hit[1]; continue
        def git(*a):
            return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=20).stdout
        trees = {}
        cur = None
        for line in git("worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                cur = line[9:]
            elif line.startswith("branch refs/heads/") and cur:
                trees[line[18:]] = cur
        found = []
        for ref in git("for-each-ref", "--format=%(refname:short)|%(committerdate:iso-strict)|%(subject)", "refs/heads").splitlines():
            name, _, rest = ref.partition("|")
            date, _, subject = rest.partition("|")
            if name in ("main", "master") or name in known or name.startswith("backup/"):
                continue
            try:
                ahead = int(git("rev-list", "--count", f"main..{name}").strip() or 0)
            except ValueError:
                continue
            if ahead == 0:
                continue
            age_d = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(date)).total_seconds() / 86400
            dirty = 0
            if name in trees and Path(trees[name]).is_dir():
                st = subprocess.run(["git", "-C", trees[name], "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, timeout=20).stdout
                dirty = sum(1 for l in st.splitlines() if l.strip())
            found.append({"project": cfg.get("project"), "branch": name, "ahead": ahead, "dirty": dirty, "last": date[:19], "subject": subject[:140],
                          "worktree": Path(trees[name]).name if name in trees else None, "age_days": round(age_d, 1),
                          "stale": age_d > 7})
        found.sort(key=lambda d: d["last"], reverse=True)
        _GIT[key] = (time.time(), found)
        out += found
    return out


GROUP_ORDER = ["trained", "pretrained", "paid", "prompt", "code", "api", "data"]


def _tool_card(con, name: str, info: dict, used_by: list[str]) -> dict:
    from . import artifacts
    row = con.execute("SELECT kind, base, version, status, about, body FROM artifact WHERE name=?", (name,)).fetchone()
    if not row:
        return {"name": name, "base": name, "version": "?", "group": info.get("group", "code"), "title": info.get("title"), "detail": ""}
    body = {} if row["kind"] == "prompt" else json.loads(row["body"])
    detail = ""
    if row["kind"] == "weights":
        m = (body.get("training") or {}).get("metrics") or {}
        key = next((k for k in ("metrics/mAP50(B)", "metrics/mAP50(M)", "accuracy", "f1", "macro_f1") if k in m), None)
        detail = f"{body.get('bytes', 0) / 1e6:.0f} MB" + (f" · {key.split('/')[-1]} {float(m[key]):.3g}" if key else "")
    elif row["kind"] == "model":
        detail = body.get("id", "")
    elif row["kind"] == "tool":
        detail = {"code": f"{len(body.get('files', {}))} 个源文件", "api": body.get("endpoint", ""),
                  "data": f"{body.get('n_files', 0)} 个文件 · {body.get('bytes', 0) / 1e6:.0f} MB"}.get(body.get("kind"), "")
    elif row["kind"] == "prompt":
        detail = f"{len(row['body'])} 字符"
    latest = con.execute("SELECT MAX(version) FROM artifact WHERE kind=? AND base=?", (row["kind"], row["base"])).fetchone()[0]
    return {"name": name, "base": row["base"], "version": row["version"], "newer": latest > row["version"],
            "group": info.get("group") or artifacts.group_of(row["kind"], {}), "title": info.get("title") or row["base"],
            "about": row["about"] or "", "detail": detail, "used_by": used_by, "status": row["status"]}


def pipeline_map(con, running: list[dict], developing: list[dict]) -> list[dict]:
    """The current workflow of every project, stage by stage: which code version runs there, what it is changing into,
    which batch is in it now, and the versioned tools it picks up. Source: the newest active workflow version."""
    from . import artifacts, modules
    artifacts.ensure(con)
    out = []
    for w in con.execute("SELECT name, base, about, body, created FROM artifact WHERE kind='workflow' AND status='active'"
                         " AND version=(SELECT MAX(version) FROM artifact a WHERE a.kind='workflow' AND a.base=artifact.base)"):
        body = json.loads(w["body"])
        box = body.get("toolbox") or {}
        stages = body["stages"]
        used = {}
        for st in stages:
            for n in st.get("prompts", []) + st.get("models", []) + st.get("weights", []) + st.get("tools", []):
                used.setdefault(n, []).append(st.get("title") or st["name"])
        cards = []
        for i, st in enumerate(stages):
            mv = st.get("module_version")
            mod = modules.get(con, mv)["module"] if mv else None
            now_v = (modules.status(con, mod)["production"] or {}) if mod else {}
            ver_about = con.execute("SELECT about FROM module_version WHERE version=?", (mv,)).fetchone() if mv else None
            mod_about = con.execute("SELECT about FROM module WHERE name=?", (mod,)).fetchone() if mod else None
            code = []
            for v in ([mv] if mv else []) + st.get("also", []):
                m_ = modules.get(con, v)["module"]
                cur = (modules.status(con, m_)["production"] or {}).get("version")
                va = con.execute("SELECT about FROM module_version WHERE version=?", (v,)).fetchone()
                code.append({"module": m_, "version": v, "about": va[0] if va else "", "stale": bool(cur and cur != v), "now": cur,
                             "developing": [{"version": d["version"], "about": d["about"]} for d in developing if d["module"] == m_]})
            tools = [_tool_card(con, n, box.get(n, {}), used.get(n, []))
                     for n in st.get("weights", []) + st.get("models", []) + st.get("prompts", []) + st.get("tools", [])]
            tools.sort(key=lambda t: (GROUP_ORDER.index(t["group"]) if t["group"] in GROUP_ORDER else 99, t["base"]))
            cards.append({"name": st["name"], "title": st.get("title") or st["name"], "steps": st.get("steps"),
                          "mascot": st.get("mascot") or ["box", "scissors", "compass", "pencil", "judge", "wrench"][i % 6],
                          "module": mod, "module_about": mod_about[0] if mod_about else None,
                          "version": mv, "version_about": ver_about[0] if ver_about else None, "paid": st.get("paid"),
                          "production_now": now_v.get("version"), "stale": any(c["stale"] for c in code),
                          "code": code, "developing": [x for c in code for x in c["developing"]],
                          "batches": [{"title": r["title"], "pct": r["pct"], "status": r["status"], "type": r["type"]}
                                      for r in running if r["stage"] == st["name"]],
                          "tools": tools})
        allt = sorted({n for n in used}, key=lambda n: (GROUP_ORDER.index(box.get(n, {}).get("group", "code"))
                                                        if box.get(n, {}).get("group", "code") in GROUP_ORDER else 99, n))
        out.append({"workflow": w["name"], "project": body.get("project"), "about": w["about"], "frozen": w["created"],
                    "stages": cards, "toolbox": [_tool_card(con, n, box.get(n, {}), used[n]) for n in allt]})
    return out


def state(con) -> dict:
    running = running_cards(con)
    developing = in_progress(con)
    return {"at": dt.datetime.now().isoformat(timespec="seconds"), "pipelines": pipeline_map(con, running, developing),
            "cost": spend.cost_view(con), "pool": {**spend.pool_usage(con), "limits": config()["pool"]},
            "notifications": _latest_notes(con),
            "in_progress": developing, "dev_history": dev_history(con), "unregistered_dev": unregistered_dev(con),
            "running": running, "unregistered_runs": sessions.unregistered_runs(con),
            "run_history": run_history(con),
            # kept for callers of the previous shape
            "developing": [], "recent": [], "production": [], "retired": [], "spend_weekly": []}


UI = Path(__file__).with_name("board_ui")                # index.html, app.css, app.js: plain files, no build step
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/state"):
            body = json.dumps(state(connect()), ensure_ascii=False, default=str).encode()
            ctype = "application/json"
        else:
            f = UI / (path.lstrip("/") or "index.html")
            if f.parent != UI or not f.is_file():
                f = UI / "index.html"                         # hash routes all land on the page
            body, ctype = f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")            # a redeployed front end shows at the next refresh
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int | None = None) -> None:
    port = port or config()["board_port"]
    print(f"reins board on http://127.0.0.1:{port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
