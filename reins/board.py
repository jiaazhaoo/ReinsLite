"""C14 board: two things, running and developing. Everything else is on a second tab.

    reins board serve [--port 8791]        read-only, refreshes every 30 s, data from the registry only
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import batches, gate, notify, spend
from .store import config, connect


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


def state(con) -> dict:
    running = []
    for b in batches.live(con):
        st = batches.status(con, b["batch_id"])
        cur = next((s for s in st["stages"] if s["status"] == "running"), None) or \
              next((s for s in st["stages"] if s["status"] == "planned"), None)
        needs = []
        if b["status"] == "paused":
            needs.append(f"paused: {b['status_reason'] or ''}")
        if any(s["paid"] for s in st["stages"]) and b["spend_cap"] <= 0:
            needs.append(f"approve spend: reins batch approve {b['batch_id']} --cap USD")
        acc = (cur["done"] + cur["skipped"] + cur["failed"]) if cur else 0
        adopted = bool(cur and cur["note"] == "adopted" and acc == 0)      # stage ran before reins: no per-case ledger yet
        health = "red" if b["status"] == "paused" or needs else "green"
        if cur and cur["failed"] > 0.05 * max(1, acc):
            health = "yellow" if health == "green" else health
        running.append({"batch_id": b["batch_id"], "type": b["type"], "purpose": b["purpose"], "status": b["status"],
                        "stage": cur["stage"] if cur else "-", "stage_ord": f"{cur['ord'] + 1}/{len(st['stages'])}" if cur else "-",
                        "progress": "adopted · ledger not wired" if adopted else f"{acc}/{b['n_cases']}",
                        "pct": round(100 * acc / max(1, b["n_cases"])),
                        "eta": _eta(con, b["batch_id"], cur["stage"], b["n_cases"], acc) if cur else "-",
                        "health": health, "needs": needs, "spent": st["batch"]["spent"], "cap": b["spend_cap"],
                        "release": b["release_name"], "mixed": bool(b["mixed_version"]), "owner": b["owner_session"],
                        "attempts": max([p["attempt"] for p in st["processes"]] or [0]),
                        "last_event": st["recent_events"][0] if st["recent_events"] else None})
    developing = []
    for v in con.execute("SELECT v.*, m.project FROM module_version v JOIN module m ON m.name=v.module"
                         " WHERE v.status='candidate' ORDER BY v.created"):
        g = gate.latest(con, v["version"])
        last = con.execute("SELECT MAX(at) FROM module_event WHERE version=?", (v["version"],)).fetchone()[0] or v["created"]
        idle_h = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() / 3600
        pins = json.loads(v["pins"])
        cost = con.execute("SELECT COALESCE(SUM(amount),0) FROM spend WHERE module_version=?", (v["version"],)).fetchone()[0]
        developing.append({"version": v["version"], "project": v["project"], "about": v["about"], "branch": v["branch"],
                           "worktree": v["worktree"], "session": v["session"], "from_batch": pins.get("from_batch"),
                           "pilot_cases": len(pins.get("pilot_cases", [])),
                           "gate": (f"{g['status']}: missed_error {g['missed_error']}/{g['base_missed_error']}, "
                                    f"review_load {g['review_load']}/{g['base_review_load']}") if g else "not run",
                           "gate_status": g["status"] if g else "none", "idle_h": round(idle_h, 1),
                           "stalled": idle_h > 24, "spent": round(cost, 3)})
    recent = [dict(r) for r in con.execute("SELECT batch_id, type, status, purpose, closed FROM batch"
                                           " WHERE status IN ('closed','done','failed') ORDER BY created DESC LIMIT 15")]
    production = [dict(r) for r in con.execute(
        "SELECT module, version, created FROM module_version v WHERE status='released' AND day || '-' || printf('%09d', seq) ="
        " (SELECT MAX(day || '-' || printf('%09d', seq)) FROM module_version x WHERE x.module=v.module AND x.status='released')"
        " ORDER BY module")]
    return {"at": dt.datetime.now().isoformat(timespec="seconds"), "running": running, "developing": developing,
            "notifications": notify.pending(con, 12), "recent": recent, "production": production,
            "spend_weekly": spend.weekly(con)}


HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Reins board</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#ddd;--card:#fafafa;--green:#2e7d32;--yellow:#b26a00;--red:#c62828;--blue:#1565c0}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121212;--fg:#eee;--mute:#999;--line:#333;--card:#1c1c1c}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
h1{font-size:18px;margin:0 0 12px}h2{font-size:15px;margin:20px 0 8px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.tabs button{background:none;border:1px solid var(--line);color:var(--fg);padding:6px 12px;cursor:pointer;border-radius:6px;margin-right:6px}
.tabs button.on{border-color:var(--blue);color:var(--blue)}
.card{border:1px solid var(--line);border-left-width:5px;background:var(--card);border-radius:8px;padding:10px 14px;margin:8px 0}
.card.green{border-left-color:var(--green)}.card.yellow{border-left-color:var(--yellow)}.card.red{border-left-color:var(--red)}.card.none{border-left-color:var(--line)}
.id{font-family:ui-monospace,monospace;font-weight:600}.mute{color:var(--mute)}.needs{color:var(--red);font-weight:600}
.bar{height:6px;background:var(--line);border-radius:3px;margin:6px 0}.bar i{display:block;height:100%;background:var(--blue);border-radius:3px}
.row{display:flex;flex-wrap:wrap;gap:4px 18px}.k{color:var(--mute)}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
.notif{padding:6px 10px;border-radius:6px;margin:4px 0;background:var(--card);border:1px solid var(--line)}.notif.action{border-color:var(--red)}.notif.warn{border-color:var(--yellow)}
</style></head><body>
<h1>Reins <span class="mute" id="at"></span></h1>
<div class="tabs"><button class="on" onclick="tab('now')">运行中 · 开发中</button><button onclick="tab('else')">历史 · 版本 · 花费</button></div>
<div id="now"><div id="notifs"></div><h2>运行中 Running</h2><div id="running"></div><h2>开发中 Developing</h2><div id="developing"></div></div>
<div id="else" style="display:none"><h2>生产版本 Production</h2><div id="production"></div><h2>最近关闭 Recent</h2><div id="recent"></div><h2>每周花费 Spend</h2><div id="spend"></div></div>
<script>
function tab(t){for(const x of ['now','else']){document.getElementById(x).style.display=x===t?'':'none'}
 document.querySelectorAll('.tabs button').forEach((b,i)=>b.classList.toggle('on',(i===0)===(t==='now')))}
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function load(){const s=await (await fetch('/api/state')).json();document.getElementById('at').textContent='· '+s.at;
 document.getElementById('notifs').innerHTML=s.notifications.filter(n=>n.level!=='info').map(n=>`<div class="notif ${n.level}"><b>${esc(n.title)}</b> <span class="mute">${esc(n.at)}</span><br>${esc(n.body)}</div>`).join('');
 document.getElementById('running').innerHTML=s.running.length?s.running.map(r=>`<div class="card ${r.health}">
  <div class="row"><span class="id">${esc(r.batch_id)}</span><span>${esc(r.type)}</span><span>[${esc(r.status)}]</span>${r.mixed?'<span class="needs">MIXED VERSION</span>':''}</div>
  <div class="mute">${esc(r.purpose)}</div>${r.needs.map(n=>`<div class="needs">⚠ ${esc(n)}</div>`).join('')}
  <div class="bar"><i style="width:${r.pct}%"></i></div>
  <div class="row"><span><span class="k">stage</span> ${esc(r.stage)} (${esc(r.stage_ord)})</span><span><span class="k">progress</span> ${esc(r.progress)}</span><span><span class="k">ETA</span> ${esc(r.eta)}</span>
  <span><span class="k">spend</span> $${r.spent} / $${r.cap}</span><span><span class="k">release</span> ${esc(r.release||'-')}</span><span><span class="k">attempts</span> ${r.attempts}</span><span><span class="k">owner</span> ${esc((r.owner||'-').slice(0,8))}</span></div>
  ${r.last_event?`<div class="mute">${esc(r.last_event.at)} ${esc(r.last_event.event)} ${esc(r.last_event.detail||'')}</div>`:''}</div>`).join(''):'<div class="mute">nothing running</div>';
 document.getElementById('developing').innerHTML=s.developing.length?s.developing.map(d=>`<div class="card ${d.gate_status}">
  <div class="row"><span class="id">${esc(d.version)}</span><span class="mute">${esc(d.project)}</span>${d.stalled?'<span class="needs">stalled '+d.idle_h+' h</span>':''}</div>
  <div>${esc(d.about)}</div>
  <div class="row"><span><span class="k">gate</span> ${esc(d.gate)}</span><span><span class="k">branch</span> ${esc(d.branch||'-')}</span><span><span class="k">session</span> ${esc((d.session||'-').slice(0,8))}</span>
  ${d.from_batch?`<span><span class="k">from</span> ${esc(d.from_batch)} (${d.pilot_cases} pilot cases)</span>`:''}<span><span class="k">spend</span> $${d.spent}</span><span><span class="k">idle</span> ${d.idle_h} h</span></div></div>`).join(''):'<div class="mute">nothing in development</div>';
 document.getElementById('production').innerHTML='<table>'+s.production.map(p=>`<tr><td>${esc(p.module)}</td><td class="id">${esc(p.version)}</td><td class="mute">${esc(p.created)}</td></tr>`).join('')+'</table>';
 document.getElementById('recent').innerHTML='<table>'+s.recent.map(r=>`<tr><td class="id">${esc(r.batch_id)}</td><td>${esc(r.status)}</td><td class="mute">${esc(r.purpose)}</td></tr>`).join('')+'</table>';
 document.getElementById('spend').innerHTML='<table><tr><th>week</th><th>project</th><th>USD</th><th>calls</th><th>cache hits</th></tr>'+s.spend_weekly.map(w=>`<tr><td>${esc(w.week)}</td><td>${esc(w.project)}</td><td>${(w.usd||0).toFixed(2)}</td><td>${w.calls}</td><td>${w.hits}</td></tr>`).join('')+'</table>';}
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
