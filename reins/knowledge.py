"""C17 shared knowledge: what one session learns about a module or a running service is where the next one looks.

Regression for 2026-10-21: a session read the review server's flags and told the user it "cannot serve two councils";
the live service on :8773 already did (a merged view built by a script under /data, case prefix left empty). It then
offered to fix the basemap snapping that another live session was already fixing. Nothing of either was recorded:
10 review / labelling services were listening on 0.0.0.0 and reins knew none of them; the developing session had no
purpose on record; the known problem belonged to no batch, so no issue could hold it.

    reins service list [--all]                       registered services, plus listening ones nobody registered
    reins service adopt PORT --name N --about '...' [--data DIR ...] [--built-by SCRIPT] [--module M]
    reins service event NAME 'what changed'          a reconfiguration, a list reassigned, data appended (restarts are seen)
    reins service retire NAME --why '...'
    reins note add SUBJECT 'fact' [--why '...']      a durable fact about a module, a service or the project
    reins note list [SUBJECT]   |   reins note retire ID --why '...'
    reins brief SUBJECT                              one screen: the live version, who is changing it, the services that
                                                     run it, open problems, facts, decisions
    reins issue open --module M | --service S --symptom '...'      a problem that belongs to no batch

A subject is a module name or a service name. The hook hands a session the brief of a subject the first time the
session edits the subject's files or names it (a module, a service name, a port) in a prompt.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from .store import ReinsError, now, session, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS service (
  name        TEXT PRIMARY KEY,
  project     TEXT,
  module      TEXT,
  port        INTEGER,
  host        TEXT,
  unit        TEXT,                                   -- the systemd unit that keeps it up, if any
  cmd         TEXT,
  cwd         TEXT,
  code_ref    TEXT,                                   -- release / candidate worktree / "main checkout" / "outside any repo"
  data        TEXT NOT NULL DEFAULT '[]',             -- JSON list of directories it serves
  built_by    TEXT,                                   -- the script that built its data
  about       TEXT NOT NULL,
  owner       TEXT,                                   -- session that registered it
  status      TEXT NOT NULL CHECK (status IN ('live', 'down', 'retired')),
  pid         INTEGER,
  started     TEXT,
  registered  TEXT NOT NULL,
  last_seen   TEXT
);
CREATE TABLE IF NOT EXISTS service_event (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  service  TEXT NOT NULL,
  event    TEXT NOT NULL,
  detail   TEXT,
  session  TEXT
);
CREATE TABLE IF NOT EXISTS note (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  subject   TEXT NOT NULL,                            -- a module, a service, or a project name
  text      TEXT NOT NULL,
  why       TEXT,
  session   TEXT,
  status    TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  retired_why TEXT
);
"""

OWN_PORTS = ("gateway_port", "board_port")
# listeners that are the machine's, not a project's (config.toml [services] ignore = ["regex", ...] adds more)
IGNORE = [r"^/usr/(libexec|lib|sbin)/", r"^/(opt|snap)/", r"gnome-", r"^/usr/bin/(cupsd|sshd|containerd|dockerd)"]


def ensure(con) -> None:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='note'").fetchone():
        con.executescript(SCHEMA)
    from . import issues
    issues.ensure(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(issue)")}
    for c in ("module", "service"):
        if c not in cols:
            con.execute(f"ALTER TABLE issue ADD COLUMN {c} TEXT")


# ------------------------------------------------------------------ what is listening
def listening() -> list[dict]:
    """TCP listeners of this user: port, host, pid, cmd, cwd, start time, systemd unit."""
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = {}
    for line in out.splitlines():
        m = re.search(r"\s(\S+):(\d+)\s+\S+\s+users:\(\(\"[^\"]*\",pid=(\d+)", line)
        if not m:
            continue
        host, port, pid = m.group(1), int(m.group(2)), int(m.group(3))
        if port in found:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            cwd = os.readlink(f"/proc/{pid}/cwd")
            started = dt.datetime.fromtimestamp(Path(f"/proc/{pid}").stat().st_mtime).isoformat(timespec="seconds")
            cg = Path(f"/proc/{pid}/cgroup").read_text()
            unit = next(iter(re.findall(r"/([\w@.\-]+\.service)", cg)[-1:]), None)
        except OSError:
            continue
        found[port] = {"port": port, "host": host.strip("[]"), "pid": pid, "cmd": cmd, "cwd": cwd, "started": started,
                       "unit": unit if unit and not unit.startswith(("user@", "app-", "vte-", "snap.")) else None}
    return sorted(found.values(), key=lambda r: r["port"])


def _code_ref(cmd: str, cwd: str) -> tuple[str, str | None, str | None]:
    """(where its code comes from, project, module) for a service's command line."""
    from . import sessions
    from . import projects
    script = next((t for t in cmd.split() if t.endswith(".py") or t.endswith(".sh")), None)
    path = script if script and script.startswith("/") else (os.path.join(cwd, script) if script else cwd)
    wt, main = sessions.repo_of(path)
    root = projects.root().resolve()
    if not wt or not (wt.resolve() == root or root in wt.resolve().parents):     # only checkouts under the code root
        return "outside any repo", None, None
    cfg = sessions.project_of(main)
    rel = os.path.relpath(path, wt)
    module = sessions.module_of(cfg, rel) if cfg else None
    name = wt.name
    if wt == main:
        ref = f"main checkout {wt} (not a release: it changes whenever main does)"
    elif "-rel-" in name:
        ref = f"release {name.split('-rel-', 1)[1]}"
    elif "-wt-" in name:
        ref = f"candidate worktree {name.split('-wt-', 1)[1]}"
    else:
        ref = str(wt)
    return ref, cfg.get("project"), module


def discover(con) -> dict:
    """Match listeners to registered services: a registered one is seen (a new pid is a restart, recorded); a gone one
    is down; a listener nobody registered is returned. reins's own gateway and board are left out."""
    ensure(con)
    from .store import config
    cfg = config()
    own = {int(cfg[k]) for k in OWN_PORTS if cfg.get(k)}
    live = {r["port"]: r for r in listening() if r["port"] not in own}
    seen, unregistered = [], []
    with tx(con):
        for s in con.execute("SELECT * FROM service WHERE status<>'retired'").fetchall():
            r = live.pop(s["port"], None) if s["port"] else None
            if r:
                if s["pid"] and s["pid"] != r["pid"]:
                    _event(con, s["name"], "restarted", f"pid {s['pid']} -> {r['pid']}", by="watchdog")
                if s["status"] == "down":
                    _event(con, s["name"], "up", f"listening on {r['port']} again", by="watchdog")
                con.execute("UPDATE service SET status='live', pid=?, cmd=?, cwd=?, started=?, unit=COALESCE(?, unit), last_seen=?"
                            " WHERE name=?", (r["pid"], r["cmd"], r["cwd"], r["started"], r["unit"], now(), s["name"]))
                seen.append(s["name"])
            elif s["status"] == "live":
                con.execute("UPDATE service SET status='down' WHERE name=?", (s["name"],))
                _event(con, s["name"], "down", f"nothing listens on {s['port']}", by="watchdog")
    ignore = [re.compile(p) for p in IGNORE + list((cfg.get("services") or {}).get("ignore", []))]
    for r in live.values():
        if any(p.search(r["cmd"]) or (r["unit"] and p.search(r["unit"])) for p in ignore):
            continue                                        # the desktop's own listeners, or what config.toml says to skip
        if r["host"] in ("0.0.0.0", "*", "::") or r["unit"]:     # reachable from the office, or kept up on purpose
            ref, project, module = _code_ref(r["cmd"], r["cwd"])
            unregistered.append({**r, "code_ref": ref, "project": project, "module": module})
    return {"seen": seen, "unregistered": unregistered}


def _event(con, service: str, event: str, detail: str = "", by: str | None = None) -> None:
    con.execute("INSERT INTO service_event (at, service, event, detail, session) VALUES (?,?,?,?,?)",
                (now(), service, event, detail, by or session()))


def adopt(con, port: int, name: str, about: str, data: list[str] | None = None, built_by: str | None = None,
          module: str | None = None) -> dict:
    ensure(con)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", name):
        raise ReinsError(f"service name {name!r}: lowercase letters, digits, - and _")
    if len(about.strip()) < 10:
        raise ReinsError("--about: one sentence saying what it serves and to whom")
    r = next((x for x in listening() if x["port"] == port), None)
    if not r:
        raise ReinsError(f"nothing of yours listens on port {port}")
    ref, project, mod = _code_ref(r["cmd"], r["cwd"])
    if not data:                                         # the directories its command line names
        data = [t for t in r["cmd"].split() if t.startswith("/data/") or t.startswith("/media/")]
    with tx(con):
        if con.execute("SELECT 1 FROM service WHERE name=? AND status<>'retired'", (name,)).fetchone():
            raise ReinsError(f"service {name} is registered already")
        if con.execute("SELECT name FROM service WHERE port=? AND status<>'retired'", (port,)).fetchone():
            raise ReinsError(f"port {port} belongs to a registered service already (reins service list)")
        con.execute("INSERT INTO service (name, project, module, port, host, unit, cmd, cwd, code_ref, data, built_by, about,"
                    " owner, status, pid, started, registered, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'live', ?,?,?,?)",
                    (name, project, module or mod, port, r["host"], r["unit"], r["cmd"], r["cwd"], ref,
                     json.dumps(data), built_by, about.strip(), session(), r["pid"], r["started"], now(), now()))
        _event(con, name, "registered", about.strip())
    return get(con, name)


def get(con, name: str) -> dict:
    ensure(con)
    row = con.execute("SELECT * FROM service WHERE name=?", (name,)).fetchone()
    if not row:
        raise ReinsError(f"no service {name!r} (reins service list)")
    d = dict(row)
    d["data"] = json.loads(d["data"])
    return d


def services(con, all_: bool = False) -> list[dict]:
    ensure(con)
    q = "SELECT * FROM service" + ("" if all_ else " WHERE status<>'retired'") + " ORDER BY port"
    return [{**dict(r), "data": json.loads(r["data"])} for r in con.execute(q)]


def service_event(con, name: str, detail: str) -> None:
    get(con, name)
    if len(detail.strip()) < 8:
        raise ReinsError("say what changed, in one line")
    with tx(con):
        _event(con, name, "changed", detail.strip())


def retire(con, name: str, why: str) -> None:
    get(con, name)
    if not why.strip():
        raise ReinsError("--why: why it is no longer needed")
    with tx(con):
        con.execute("UPDATE service SET status='retired' WHERE name=?", (name,))
        _event(con, name, "retired", why.strip())


# ------------------------------------------------------------------ facts
def _subjects(con) -> set[str]:
    ensure(con)
    s = {r[0] for r in con.execute("SELECT name FROM module")} | {r[0] for r in con.execute("SELECT name FROM service")}
    s |= {r[0] for r in con.execute("SELECT DISTINCT project FROM module")}
    return s


def note_add(con, subject: str, text: str, why: str = "") -> int:
    ensure(con)
    if subject not in _subjects(con):
        raise ReinsError(f"{subject!r} is neither a module, a service nor a project (reins service list, reins dev list)")
    if len(text.strip()) < 15:
        raise ReinsError("a fact is a sentence someone can act on")
    with tx(con):
        cur = con.execute("INSERT INTO note (at, subject, text, why, session) VALUES (?,?,?,?,?)",
                          (now(), subject, text.strip(), why.strip() or None, session()))
    return cur.lastrowid


def notes(con, subject: str | None = None, all_: bool = False) -> list[dict]:
    ensure(con)
    q, a = "SELECT * FROM note WHERE 1=1", []
    if subject:
        q += " AND subject=?"; a.append(subject)
    if not all_:
        q += " AND status='active'"
    return [dict(r) for r in con.execute(q + " ORDER BY id DESC", a)]


def note_retire(con, nid: int, why: str) -> None:
    ensure(con)
    if not why.strip():
        raise ReinsError("--why: what replaced it or why it is no longer true")
    with tx(con):
        if not con.execute("UPDATE note SET status='retired', retired_why=? WHERE id=? AND status='active'", (why, nid)).rowcount:
            raise ReinsError(f"no active note #{nid}")


# ------------------------------------------------------------------ the brief
def _ago(ts: str | None) -> str:
    if not ts:
        return "?"
    h = (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() / 3600
    return f"{h * 60:.0f} min ago" if h < 1 else f"{h:.0f} h ago" if h < 48 else f"{h / 24:.0f} days ago"


def brief(con, subject: str) -> str:
    """Everything known about a module or a service, on one screen, for a session about to touch it."""
    ensure(con)
    from . import issues, sessions
    svc = con.execute("SELECT * FROM service WHERE name=?", (subject,)).fetchone()
    module = svc["module"] if svc else subject
    is_module = bool(con.execute("SELECT 1 FROM module WHERE name=?", (module,)).fetchone()) if module else False
    if not svc and not is_module:
        raise ReinsError(f"{subject!r} is neither a module nor a service")
    L = [f"== brief: {subject}" + (f" (service of module {module})" if svc and module else "")]
    if is_module:
        m = con.execute("SELECT * FROM module WHERE name=?", (module,)).fetchone()
        L.append(f"module {module}: {m['about']}")
        rel = con.execute("SELECT version, about, created FROM module_version WHERE module=? AND status='released'"
                          " ORDER BY created DESC LIMIT 5", (module,)).fetchall()
        if rel:
            L.append("released, newest first:")
            L += [f"  {r['version']}  {r['about'][:110]}" for r in rel]
        cands = con.execute("SELECT version, about, session, created FROM module_version WHERE module=? AND status='candidate'"
                            " ORDER BY created DESC", (module,)).fetchall()
        live = {s["id"]: s for s in sessions.list_(con)}
        for c in cands:
            who = live.get(c["session"])
            L.append(f"IN PROGRESS {c['version']} by session {str(c['session'])[:8]}"
                     f" ({who['title'] + ', ' + who['status'] if who else 'session ended'}): {c['about'][:110]}")
        since = (dt.datetime.now() - dt.timedelta(hours=24)).isoformat(timespec="seconds")
        for s in con.execute("SELECT session_id, MAX(at) at, COUNT(*) n FROM session_event WHERE module=? AND kind='edit' AND at>?"
                             " GROUP BY session_id ORDER BY 2 DESC", (module, since)):
            if s["session_id"] in {c["session"] for c in cands} or s["session_id"] == session():
                continue
            who = live.get(s["session_id"])
            L.append(f"EDITING (no version registered) session {s['session_id'][:8]}: {s['n']} edits, last {_ago(s['at'])}"
                     + (f" — {who['title']}" if who else ""))
    svcs = [dict(svc)] if svc else [dict(r) for r in con.execute(
        "SELECT * FROM service WHERE module=? AND status<>'retired' ORDER BY port", (module,))]
    for s in svcs:
        L.append(f"service {s['name']} :{s['port']} [{s['status']}] {s['about']}")
        L.append(f"    code: {s['code_ref']}; data: {', '.join(json.loads(s['data'])) or '-'}"
                 + (f"; built by {s['built_by']}" if s["built_by"] else "") + (f"; unit {s['unit']}" if s["unit"] else ""))
        for e in con.execute("SELECT at, event, detail FROM service_event WHERE service=? ORDER BY id DESC LIMIT 4", (s["name"],)):
            L.append(f"    {e['at'][:16]} {e['event']}: {(e['detail'] or '')[:110]}")
    names_ = [module] if is_module else []
    names_ += [s["name"] for s in svcs]
    open_ = [i for i in issues.list_(con) if i.get("module") in names_ or i.get("service") in names_]
    for i in open_:
        L.append(f"OPEN PROBLEM #{i['id']} [{i['status']}]"
                 + (f" being fixed by {i['version']}" if i.get("version") else "") + f": {i['symptom'][:140]}")
    facts = [n for s in names_ for n in notes(con, s)]
    if facts:
        L.append("facts:")
        L += [f"  #{n['id']} ({n['subject']}, {n['at'][:10]}) {n['text']}" for n in facts[:12]]
    if is_module:
        for d in con.execute("SELECT id, at, text FROM decision WHERE status='active' AND text LIKE ? ORDER BY id DESC LIMIT 3",
                             (f"%{module}%",)):
            L.append(f"decision #{d['id']} ({d['at'][:10]}): {d['text'][:140]}")
    L.append(f"add what you learn: reins note add {subject} '...';  a problem: reins issue open "
             f"--{'service' if svc else 'module'} {subject} --symptom '...'")
    return "\n".join(L)


def mentioned(con, text: str) -> list[str]:
    """The subjects a prompt names: a registered service by name or port, or a module by name (whole words)."""
    ensure(con)
    out = []
    for s in con.execute("SELECT name, port, module FROM service WHERE status<>'retired'"):
        if re.search(rf"(?<![\w-]){re.escape(s['name'])}(?![\w-])", text) or (s["port"] and re.search(rf"(?<!\d){s['port']}(?!\d)", text)):
            out.append(s["name"])
    for m in con.execute("SELECT name FROM module"):
        if len(m[0]) >= 4 and re.search(rf"(?<![\w-]){re.escape(m[0])}(?![\w-])", text) and m[0] not in out:
            out.append(m[0])
    return out


def briefed(con, sid: str, subject: str) -> bool:
    """Whether this session was handed the subject's brief already; records it if not."""
    with tx(con):
        if con.execute("SELECT 1 FROM session_event WHERE session_id=? AND kind='brief' AND detail=?", (sid, subject)).fetchone():
            return True
        con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?,?, 'brief', ?)", (now(), sid, subject))
    return False


def watch(con) -> list[str]:
    """The watchdog's pass: registered services seen / restarted / down; a new unregistered listener is told once a week."""
    from . import notify
    d = discover(con)
    out = []
    for u in d["unregistered"]:
        if notify.send(con, f"service_unregistered:{u['port']}", "warn", f"port {u['port']} serves something nobody registered",
                       f"{u['cmd'][:160]} ({u['code_ref']}). If it is kept: reins service adopt {u['port']} --name N --about '...'",
                       cooldown_min=7 * 24 * 60):
            out.append(f"unregistered service on :{u['port']}")
    return out
