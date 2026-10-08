"""C14 board: one page, four blocks, each answering one question.

    成本        how much money is left, what was spent today, where it went            (balance samples + ledger)
    正在开发    who is changing what, how far, any clash                                (sessions index + candidates)
    开发记录    what reached production and what each change did                       (released versions + gates)
    运行        where the running batches are; what earlier batches produced           (batch ledger + acceptance)

    reins board serve [--port 8791]        read-only, refreshes every 30 s, data from the registry only
No session ids, paths or timelines are shown (user, 2026-10-07): sessions are only the index behind these blocks.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
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
                    "aliases": aliases[1:]})
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
            "in_progress": developing, "dev_history": dev_history(con),
            "running": running, "unregistered_runs": sessions.unregistered_runs(con),
            "run_history": run_history(con),
            # kept for callers of the previous shape
            "developing": [], "recent": [], "production": [], "retired": [], "spend_weekly": []}


HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Reins</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#ddd;--card:#fafafa;--green:#2e7d32;--yellow:#b26a00;--red:#c62828;--blue:#1565c0}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121212;--fg:#eee;--mute:#999;--line:#333;--card:#1c1c1c}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;max-width:1480px}
h1{font-size:18px;margin:0 0 12px}h2{font-size:15px;margin:22px 0 8px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
h2 small{text-transform:none;letter-spacing:0;font-weight:400;margin-left:8px}
.card{border:1px solid var(--line);border-left-width:5px;background:var(--card);border-radius:8px;padding:10px 14px;margin:8px 0}
.card.green{border-left-color:var(--green)}.card.yellow{border-left-color:var(--yellow)}.card.red{border-left-color:var(--red)}.card.none{border-left-color:var(--line)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.cost{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:12px 14px}
.cost .bal{font-size:26px;font-weight:700;margin:2px 0}.cost .bal.low{color:var(--red)}.cost .name{font-weight:600;text-transform:capitalize}
.cost .row2{display:flex;gap:18px;color:var(--mute);font-size:13px;flex-wrap:wrap}.cost b{color:var(--fg)}
.id{font-family:ui-monospace,monospace;font-weight:600}.mute{color:var(--mute)}.needs{color:var(--red);font-weight:600}
.big{font-size:17px;font-weight:700;margin-bottom:2px}.stat{display:flex;flex-wrap:wrap;gap:4px 22px;margin:6px 0}
.pill{font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px;border:1px solid var(--line);vertical-align:middle;margin-left:4px}
.pill.running,.pill.ok{color:var(--green);border-color:var(--green)}.pill.paused,.pill.bad{color:var(--red);border-color:var(--red)}.pill.warn{color:var(--yellow);border-color:var(--yellow)}
.bar{height:6px;background:var(--line);border-radius:3px;margin:6px 0}.bar i{display:block;height:100%;background:var(--blue);border-radius:3px}
.row{display:flex;flex-wrap:wrap;gap:4px 18px}.k{color:var(--mute)}.small{font-size:12px;margin-top:4px}
.probe{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font:12px/1.4 ui-monospace,monospace;overflow-x:auto;margin:6px 0;max-height:260px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:600}
.flow{display:flex;gap:0;overflow-x:auto;padding:4px 2px 10px;align-items:stretch}
.stage{flex:1 0 210px;border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:5px}
.stage.busy{border-color:var(--blue)}.stage.dev{box-shadow:inset 0 3px 0 var(--yellow)}
.link{flex:0 0 26px;display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:18px}
.crab{display:flex;justify-content:center;height:78px}.crab svg{width:104px;height:78px;shape-rendering:crispEdges}
.stage.busy .crab svg{animation:bob .9s ease-in-out infinite}@keyframes bob{50%{transform:translateY(-4px)}}
.stitle{font-size:16px;font-weight:700}.stitle .mono{font:12px ui-monospace,monospace;color:var(--mute);font-weight:400;margin-left:6px}
.ver{font:12px ui-monospace,monospace;background:var(--bg);border:1px solid var(--line);border-radius:5px;padding:2px 6px;word-break:break-all}
.shelf{font-size:11px;color:var(--mute);margin-top:4px}.chips{display:flex;flex-wrap:wrap;gap:3px}
.chip{font-size:11.5px;padding:1px 6px;border-radius:9px;border:1px solid var(--line);background:var(--bg);cursor:default;white-space:nowrap}
.chip b{font-weight:600}.chip.trained{border-color:#d97757;color:#b4532f}.chip.pretrained{border-color:#5b8def;color:#2f5fbf}
.chip.paid{border-color:#c0392b;color:#c0392b}.chip.prompt{border-color:#8e6cc8;color:#6c48a8}.chip.code{border-color:#888;color:var(--fg)}
.chip.api{border-color:#1f9e89;color:#147a6a}.chip.data{border-color:#9a7b2f;color:#7a5f1f}.chip .new{color:var(--yellow)}
.legend{display:flex;flex-wrap:wrap;gap:6px 10px;font-size:12px;color:var(--mute);margin:2px 0 6px}
details.box{margin:6px 0}details.box summary{cursor:pointer;color:var(--mute)}
.notif{padding:6px 10px;border-radius:6px;margin:4px 0;background:var(--card);border:1px solid var(--line)}.notif.action{border-color:var(--red)}.notif.warn{border-color:var(--yellow)}
</style></head><body>
<h1>Reins <span class="mute" id="at"></span></h1>
<div id="notifs"></div>
<h2>流程图谱 <small id="wfnote">每个阶段现在跑的代码版本，和它从工具箱里拿的东西</small></h2><div id="map"></div>
<h2>成本 <small id="costnote"></small></h2><div id="pool" class="card none"></div><div class="grid" id="cost"></div>
<h2>正在开发 <small>会话还在改、还没进生产的</small></h2><div id="inprogress"></div>
<h2>开发记录 <small>已进生产的版本</small></h2><div id="devhist"></div>
<h2>运行 <small>正在跑的批次</small></h2><div id="running"></div>
<h2>运行记录 <small>跑完的批次</small></h2><div id="runhist"></div>
<script>
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function usd(v){return v==null?'—':'$'+Number(v).toFixed(2)}
function d(ts){return ts?esc(ts.slice(5,16).replace('T',' ')):'—'}
const ST={running:'运行中',paused:'暂停',open:'未开始',closed:'已关闭',done:'完成',failed:'失败'};
const TYPE={production:'生产',rework:'返工',experiment:'实验',pilot:'试跑',eval:'评测',smoke:'冒烟',drift:'漂移',benchmark_build:'建基准'};
const DEC={ship:'交付',ship_with_note:'带说明交付',rework:'返工'};

const SHELF={trained:'自训模型',pretrained:'开源模型',paid:'付费模型',prompt:'提示词',code:'代码工具',api:'外部接口',data:'数据'};
const BODY='#D97757',EYE='#1d1d1f';
const HATS={cap:[[6,2,8,2,'#3b6fd8'],[13,3,4,1,'#3b6fd8']],beret:[[7,2,7,2,'#7a4fc4'],[10,1,1,1,'#7a4fc4']],
 explorer:[[7,1,6,2,'#b59a5a'],[5,3,10,1,'#9c8247'],[7,2,6,.5,'#6b5a2e']],headband:[[6,4,8,1,'#d23b3b'],[14,3.5,2,1,'#d23b3b'],[15,4.5,1.5,1,'#d23b3b']],
 mortar:[[5,2,10,1,'#26262a'],[8,3,4,1,'#26262a'],[14.5,2.5,.6,2.5,'#e0b030']],helmet:[[6,2,8,2,'#f2c230'],[9,1.5,2,.6,'#f2c230']]};
const ITEMS={clipboard:[[16,5,3.4,5,'#f3efe4'],[17.2,4.4,1,1,'#777'],[16.5,6.5,2.4,.5,'#999'],[16.5,8,2.4,.5,'#999']],
 scissors:[[1,3,.8,4,'#9aa0a6'],[2.6,3,.8,4,'#9aa0a6'],[.4,7,1.6,1.6,'#d23b3b'],[2.2,7,1.6,1.6,'#d23b3b']],
 map:[[16,4.5,4,4.5,'#86c58a'],[16.6,6,2.8,.6,'#3b6fd8'],[18.2,5,.6,3.5,'#3b6fd8'],[17,4.7,.9,.9,'#d23b3b']],
 pencil:[[16.2,2.5,1,6,'#f2c230'],[16.2,8.5,1,1,'#d23b3b'],[1,13.2,18,.6,'#d23b3b']],
 judge:[[7,5.4,3,3.2,'#26262a'],[7.5,5.9,2,2.2,'#cfe8ff'],[10,5.4,3,3.2,'#26262a'],[10.5,5.9,2,2.2,'#cfe8ff'],[9.6,6.4,.8,.5,'#26262a'],[1.8,3,.6,7,'#777'],[2.4,3,3,2,'#2e9e5b']],
 wrench:[[16.3,4,1,5,'#9aa0a6'],[15.6,3,2.4,1.3,'#9aa0a6']]};
const MASCOT={box:['cap','clipboard'],scissors:['beret','scissors'],compass:['explorer','map'],pencil:['headband','pencil'],judge:['mortar','judge'],wrench:['helmet','wrench']};
function crab(kind){const [h,it]=MASCOT[kind]||MASCOT.wrench;const r=(a)=>a.map(([x,y,w,hh,c])=>`<rect x="${x}" y="${y}" width="${w}" height="${hh}" fill="${c}"/>`).join('');
 const body=[[6,4,8,6,BODY],[4,6,2,2,BODY],[14,6,2,2,BODY],[6,10,1,2,BODY],[8,10,1,2,BODY],[11,10,1,2,BODY],[13,10,1,2,BODY]];
 const eyes=[[8,6,1,2,EYE],[11,6,1,2,EYE]];
 return `<svg viewBox="0 0 20 15" aria-hidden="true">${r(body)}${it==='judge'?r(ITEMS.judge.slice(0,5))+r(eyes):r(eyes)}${r(HATS[h]||[])}${r(it==='judge'?ITEMS.judge.slice(5):ITEMS[it]||[])}</svg>`;}
function chip(t){const tip=[t.title,t.about,t.detail,t.used_by&&t.used_by.length>1?'也用于：'+t.used_by.join('、'):''].filter(Boolean).join('\\n');
 return `<span class="chip ${t.group}" title="${esc(tip)}">${esc(t.title)} <b>v${t.version}</b>${t.newer?'<span class="new"> ↑</span>':''}</span>`;}
function shelves(tools){const g={};tools.forEach(t=>(g[t.group]=g[t.group]||[]).push(t));
 return Object.keys(SHELF).filter(k=>g[k]).map(k=>`<div class="shelf">${SHELF[k]}</div><div class="chips">${g[k].map(chip).join('')}</div>`).join('');}
function drawMap(P){if(!P.length){document.getElementById('map').innerHTML='<div class="mute">还没有冻结的工作流：reins workflow freeze NAME</div>';return;}
 document.getElementById('map').innerHTML=P.map(w=>`<div class="legend"><b style="color:var(--fg)">${esc(w.project||'')}</b><span>${esc(w.about||'')}</span><span class="id">${esc(w.workflow)}</span><span>冻结于 ${d(w.frozen)}</span>${Object.entries(SHELF).map(([k,v])=>`<span class="chip ${k}">${v}</span>`).join('')}</div>
 <div class="flow">${w.stages.map((st,i)=>`${i?'<div class="link">→</div>':''}<div class="stage ${st.batches.length?'busy':''} ${st.developing.length?'dev':''}">
  <div class="crab">${crab(st.mascot)}</div>
  <div class="stitle">${esc(st.title)}<span class="mono">${esc(st.name)}${st.steps?' · 第 '+esc(st.steps)+' 步':''}</span></div>
  ${st.module_about?`<div class="small">${esc(st.module_about)}</div>`:''}
  ${st.code.length?st.code.map(c=>`<div class="ver" title="${esc(c.about)}">${esc(c.version)}</div><div class="mute small">${esc(c.about)}</div>${c.stale?`<div class="needs small">生产已是 ${esc(c.now)}，工作流未重新冻结</div>`:''}`).join(''):'<div class="mute small">无代码模块（流水线自带）</div>'}
  ${st.batches.map(b=>`<div class="small"><span class="pill running">工作中</span> ${esc(b.title)} · ${b.pct}%</div>`).join('')}
  ${st.developing.map(v=>`<div class="small" style="color:var(--yellow)">🔧 开发中 ${esc(v.version||'')}${v.about?'：'+esc(v.about):''}</div>`).join('')}
  ${st.paid?'<div class="small mute">付费阶段：先批花费上限</div>':''}
  ${shelves(st.tools)}</div>`).join('')}</div>
 <details class="box"><summary>完整工具箱（${w.toolbox.length} 件，按架子分）</summary><table><tr><th>架子</th><th>工具</th><th>版本</th><th>做什么</th><th>细节</th><th>哪些阶段用</th></tr>${w.toolbox.map(t=>`<tr><td>${SHELF[t.group]||esc(t.group)}</td><td><b>${esc(t.title)}</b></td><td class="id">${esc(t.name)}${t.newer?' <span class="pill warn">有新版本</span>':''}</td><td>${esc(t.about)}</td><td class="mute">${esc(t.detail)}</td><td>${esc((t.used_by||[]).join('、'))}</td></tr>`).join('')}</table></details>`).join('');}
async function load(){const s=await (await fetch('/api/state')).json();document.getElementById('at').textContent='· '+s.at.replace('T',' ');
 drawMap(s.pipelines||[]);
 document.getElementById('notifs').innerHTML=s.notifications.map(n=>`<div class="notif ${n.level}"><b>${esc(n.title)}</b> <span class="mute">${d(n.at)}</span><br>${esc(n.body)}</div>`).join('');
 document.getElementById('cost').innerHTML=s.cost.map(c=>`<div class="cost"><div class="name">${esc(c.provider)} ${!c.key_present?'<span class="pill">未配置 key</span>':!c.has_endpoint?'<span class="pill">无余额接口</span>':c.error?'<span class="pill bad">查询失败</span>':''}</div>
  <div class="bal ${c.balance!=null&&c.balance<10?'low':''}">${c.has_endpoint?usd(c.balance):'—'}</div>
  <div class="row2"><span>今日 <b>${usd(c.spent_today)}</b></span><span>本周 <b>${usd(c.spent_week)}</b></span><span>今日调用 <b>${c.calls_today}</b></span></div>
  <div class="row2 small"><span>${c.spend_source==='balance'?'按余额变化计，含未经网关的调用':c.per_call?'无余额接口：按调用数 × 牌价估算':'仅网关记账'}</span>${c.balance_at?`<span>余额 ${d(c.balance_at)}</span>`:''}</div>
  ${c.by_batch.length?`<div class="small mute">本周按批次：${c.by_batch.map(b=>esc(b.batch_id)+' '+usd(b.usd)).join('；')}</div>`:''}</div>`).join('');
 document.getElementById('costnote').textContent='余额每 10 分钟查一次（免费接口）';
 const P=s.pool,L=P.limits;const pct=(a,b)=>Math.min(100,Math.round(100*a/b));
 document.getElementById('pool').innerHTML=`<div class="row"><b>预算池（所有会话合计，经网关）</b><span>近 1 小时 <b>${usd(P.hour)}</b> / ${usd(L.hourly_cap)}</span><span>今日 <b>${usd(P.today)}</b> / ${usd(L.daily_cap)}</span><span>本周 <b>${usd(P.week)}</b> / ${usd(L.weekly_cap)}</span><span>未关闭批次的上限合计 <b>${usd(P.open_caps)}</b></span><span class="mute">单批 ≤ ${usd(L.max_batch_cap)}，单会话每日 ≤ ${usd(L.per_session_daily_cap)}</span></div><div class="bar"><i style="width:${pct(P.today,L.daily_cap)}%"></i></div>`;
 document.getElementById('inprogress').innerHTML=s.in_progress.length?s.in_progress.map(p=>`<div class="card ${p.conflicts.length?'red':(p.registered?'green':'yellow')}">
  <div class="big">${esc(p.module)} <span class="pill ${p.registered?'ok':'warn'}">${p.registered?'候选版本 '+esc(p.version):'还没登记版本'}</span>${p.stalled?' <span class="pill bad">停滞 '+p.idle_h+' h</span>':''}</div>
  <div>${esc(p.about)}</div>${p.module_about?`<div class="mute small">模块：${esc(p.module_about)}</div>`:''}
  ${p.conflicts.map(c=>`<div class="needs">⚠ ${esc(c)}</div>`).join('')}
  <div class="row mute small"><span>改了 ${p.files} 个文件</span><span>门禁：${esc(p.gate)}</span>${p.issue?`<span>修问题单 #${p.issue}</span>`:''}${p.from_batch?`<span>起因：${esc(p.from_batch)}</span>`:''}<span>最后改动 ${d(p.last)}</span></div></div>`).join(''):'<div class="mute">没有进行中的开发</div>';
 document.getElementById('devhist').innerHTML=s.dev_history.length?'<table><tr><th>时间</th><th>模块</th><th>版本</th><th>这个版本做了什么</th><th>门禁</th></tr>'+s.dev_history.map(v=>`<tr><td>${d(v.released_at)}</td><td><b>${esc(v.module)}</b></td><td class="id">${esc(v.version)}${v.status!=='released'?' <span class="pill bad">'+esc(v.status)+'</span>':''}</td><td>${esc(v.about)}${v.from_batch?'<div class="mute small">起因：'+esc(v.from_batch)+'</div>':''}${v.artifacts&&v.artifacts.length?'<div class="mute small">用：'+v.artifacts.map(esc).join('、')+'</div>':''}${v.why_out?'<div class="mute small">'+esc(v.why_out)+'</div>':''}</td><td class="mute">${esc(v.gate)}</td></tr>`).join('')+'</table>':'<div class="mute">还没有发布记录</div>';
 const runs=s.running.map(r=>`<div class="card ${r.health}">
  <div class="big">${esc(r.title)} <span class="pill ${r.status}">${ST[r.status]||esc(r.status)}</span><span class="pill">${TYPE[r.type]||esc(r.type)}</span>${r.mixed?' <span class="pill warn">中途换过版本</span>':''}</div>
  <div>${esc(r.purpose)}</div>
  <div class="stat"><span><b>${r.n_cases}</b> cases</span>${r.n_skipped?`<span><b>${r.n_skipped}</b> 不进流程 <span class="mute">(${esc(r.skip_reasons.join('; '))})</span></span>`:''}<span>当前阶段 <b>${esc(r.stage)}</b> (${esc(r.stage_ord)})，已 ${esc(r.stage_age)}</span></div>
  ${r.status_reason?`<div class="needs">⏸ ${esc(r.status_reason)}</div>`:''}${r.needs.map(n=>`<div class="needs">⚠ ${esc(n)}</div>`).join('')}
  ${r.issues.map(i=>`<div class="${i.status==='fixed'?'mute':'needs'} small">${i.status==='fixed'?'✓ 已修复待确认':i.status==='in_progress'?'🔧 修复中':'⚠ 待修'} #${i.id}（${i.n} cases）${esc(i.symptom)}${i.version?' → '+esc(i.version):''}</div>`).join('')}
  ${r.adopted?`<div class="mute small">${esc(r.progress)}</div>`:`<div class="bar"><i style="width:${r.pct}%"></i></div><div><span class="k">进度</span> ${esc(r.progress)} · <span class="k">ETA</span> ${esc(r.eta)}</div>`}
  ${r.probe.length?`<pre class="probe">${esc(r.probe.join('\\n'))}</pre>`:''}
  <div class="row mute small"><span class="id">${esc(r.batch_id)}</span><span>版本 ${esc(r.release||'-')}</span><span>花费 ${usd(r.spent)} / ${usd(r.cap)}</span><span>用时 ${r.time_used_h} h${r.time_budget_h?' / '+r.time_budget_h+' h':''}</span>${r.aliases.length?`<span>也叫 ${esc(r.aliases.join(', '))}</span>`:''}</div></div>`).join('');
 const unreg=s.unregistered_runs.map(u=>`<div class="card yellow"><div class="big">${esc(u.kind)}（未登记） <span class="pill">${esc(u.at.slice(11,16))}</span></div><div class="mute">${esc(u.what)}</div><div class="needs">⚠ 没有经过 reins 启动：看不到进度、花费和 case 账本</div></div>`).join('');
 document.getElementById('running').innerHTML=(runs+unreg)||'<div class="mute">没有在跑的批次</div>';
 document.getElementById('runhist').innerHTML=s.run_history.length?'<table><tr><th>结束</th><th>批次</th><th>类型</th><th>cases</th><th>结果</th><th>花费</th><th>用时</th><th>验收</th></tr>'+s.run_history.map(h=>`<tr><td>${d(h.closed)}</td><td><b>${esc(h.title)}</b><div class="mute small id">${esc(h.batch_id)}</div></td><td>${TYPE[h.type]||esc(h.type)}</td><td>${h.n_cases}</td><td>${h.stages_done}/${h.stages} 阶段${h.done!=null?`，完成 ${h.done}${h.skipped?' · 跳过 '+h.skipped:''}${h.failed?' · <span class="needs">失败 '+h.failed+'</span>':''}`:''}${h.mixed?' <span class="pill warn">换过版本</span>':''}</td><td>${usd(h.spent)}</td><td>${h.hours!=null?h.hours+' h':'—'}</td><td>${h.decision?DEC[h.decision]||esc(h.decision):'<span class="mute">未验收</span>'}</td></tr>`).join('')+'</table>':'<div class="mute">还没有跑完的批次</div>';}
load();setInterval(load,30000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(state(connect()), ensure_ascii=False, default=str).encode()
            ctype = "application/json"
        else:
            body, ctype = HTML.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int | None = None) -> None:
    port = port or config()["board_port"]
    print(f"reins board on http://127.0.0.1:{port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
