"""C15 sessions: the provenance index of every development and every run.

Development items (module versions, or unregistered edits to a module) and runs (batches started, paused, stopped,
experiments) are what the system manages. A session is only the index saying where an action came from: which
Claude Code session did it, and where that session's transcript is, so "how did this come about" can be answered.
Internally a session is classified as one of (used only to attach it to the right item):
  develop      updating one sub-module (registered version, or inferred from the files it edits)
  run          running, pausing or stopping a batch
  experiment   a pilot / experiment batch, or one-off scripts outside the pipeline
  analysis     reading only
Its timeline (edits, runs started and stopped, experiments, merges, blocked actions) comes from Claude Code hooks
(hooks/session_hook.py), so a session is seen whether or not it follows the rules. Conflicts between live sessions
are computed from the timelines (conflicts()).

    reins session list [--all]               live sessions with role, purpose, last action
    reins session show ID                    one session's timeline
    reins session bind --role R --purpose '...' [--module M | --version V | --batch B]     a session says what it is for
    reins session conflicts                  conflicts between live sessions
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

from .store import ReinsError, now, tx

IDLE_MIN = 30            # no event for this long -> idle
CONFLICT_WINDOW_H = 24   # edits older than this do not conflict

# Bash commands are classified by the programs they execute, never by words that merely appear in them
# (`cat tools/run_local_qa.sh` reads the script; it does not run it). Here-doc bodies are data, not commands.
RUN_PROGRAMS = {"run_local_qa.sh", "run_rework.sh", "run_batch.sh", "run_batch3.sh", "run_portal_part.sh",
                "boundary_lab.py", "qa_judge.py", "vlm_crops.py", "vet_crops.py", "case_classify.py", "qa_input_polygon.py"}
RUN_MODULES = {"e2e_plan_extract.georef.batch", "e2e_plan_extract.georef.read_vlm", "e2e_plan_extract.georef.ocr_rotated"}
WRAPPERS = {"nohup", "setsid", "time", "nice", "ionice", "exec", "env", "sudo", "systemd-run", "stdbuf", "(", "{"}
DETACHERS = {"nohup", "setsid", "disown"}
INTERPRETERS = re.compile(r"^(python[0-9.]*|\S*/python[0-9.]*|bash|sh|zsh|\$\{?\w*PY\}?)$")
EXPERIMENT_TYPES = re.compile(r"--type\s+(experiment|pilot|smoke|eval)\b")
BATCH_IN = re.compile(r"\b([a-z][a-z0-9_]*-[a-z][a-z0-9_]*-(?:production|rework|experiment|pilot|eval|benchmark_build|smoke|drift)-\d{8}-\d+)\b")
READ_PROGRAMS = {"ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "du", "df", "ps", "free", "nvidia-smi", "stat",
                 "file", "which", "echo", "pwd", "sed", "awk", "sort", "uniq", "cut", "tr", "diff", "jq", "less", "curl",
                 "readlink", "realpath", "date", "test", "true", "sleep", "printf", "cd", "[", "column", "xargs"}


def _unquoted_lines(cmd: str) -> list[str]:
    """Split on newlines that are outside quotes (a newline inside a quoted commit message is text)."""
    out, buf, q, esc = [], [], None, False
    for ch in cmd:
        if esc:
            buf.append(ch); esc = False; continue
        if ch == "\\" and q != "'":
            buf.append(ch); esc = True; continue
        if q:
            if ch == q:
                q = None
            buf.append(ch); continue
        if ch in ("'", '"'):
            q = ch; buf.append(ch); continue
        if ch == "\n":
            out.append("".join(buf)); buf = []; continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def segments(cmd: str) -> list[dict]:
    """Each command segment: {"argv": [program, args...], "detached": bool} after env assignments and wrappers.
    Operators are recognised only outside quotes; here-doc bodies are data, not commands."""
    import shlex
    cmd = re.split(r"<<-?\s*['\"]?\w+['\"]?", cmd, maxsplit=1)[0]
    out = []
    for line in _unquoted_lines(cmd):
        if not line.strip():
            continue
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        try:
            toks = list(lex)
        except ValueError:
            toks = line.split()
        cur, detached = [], False
        def flush(det):
            nonlocal cur
            t = cur
            cur = []
            while t:
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t[0]):
                    t = t[1:]
                elif t[0] in WRAPPERS or t[0] in DETACHERS:
                    det |= t[0] in DETACHERS
                    t = t[1:]
                elif t[0] == "timeout":
                    t = t[1:]
                    while t and re.match(r"^(-\S+|\d+(\.\d+)?[smhd]?)$", t[0]):
                        t = t[1:]
                else:
                    break
            if t:
                out.append({"argv": t, "detached": det})
        for tok in toks:
            if tok in (";", "&&", "||", "|", "&"):
                flush(tok == "&")
            elif tok in ("(", ")"):
                continue
            else:
                cur.append(tok)
        flush(False)
    return out


def runs_pipeline(argv: list[str]) -> bool:
    if Path(argv[0]).name in RUN_PROGRAMS:
        return True
    if INTERPRETERS.match(argv[0]) and len(argv) > 1:
        if argv[1] == "-m" and len(argv) > 2:
            return argv[2] in RUN_MODULES
        return Path(argv[1]).name in RUN_PROGRAMS
    return False


GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def git_verb(rest: list[str]) -> str:
    """The git subcommand: skip options, and the values of options that take one (-c key=value, -C path)."""
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in GIT_VALUE_OPTS:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a
    return ""


def _kind(argv: list[str]) -> str:
    p, rest = Path(argv[0]).name, argv[1:]
    if p == "reins" or (INTERPRETERS.match(argv[0]) and rest[:2] == ["-m", "reins"]):
        r = rest[2:] if p != "reins" else rest
        if r[:1] == ["run"] or r[:2] == ["ctl", "resume"]:
            return "run_start"
        if r[:2] in (["ctl", "stop"], ["ctl", "pause"]):
            return "run_stop"
        if r[:2] == ["dev", "finish"]:
            return "merge"
        if r[:1] == ["dev"] and r[1:2] and r[1] in ("start", "abandon", "release"):
            return "dev"
        if r[:2] == ["batch", "open"] and EXPERIMENT_TYPES.search(" ".join(r)):
            return "experiment"
        return "read"
    if p in ("kill", "pkill", "killall"):
        return "run_stop"
    if p == "qa_ctl.sh":
        return "run_stop" if rest[:1] and rest[0] in ("stop", "pause") else "run_start" if rest[:1] == ["resume"] else "read"
    if p == "dev.py" or (INTERPRETERS.match(argv[0]) and len(argv) > 1 and Path(argv[1]).name == "dev.py"):
        verb = (argv[2] if Path(argv[0]).name != "dev.py" else argv[1]) if len(argv) > 2 or p == "dev.py" else ""
        return "merge" if verb == "finish" else "dev" if verb in ("start", "abandon", "release") else "read"
    if runs_pipeline(argv):
        return "experiment" if re.search(r"-probe-|/experiments?/|\btryrun\b", " ".join(argv)) else "run_start"
    if p == "git":
        verb = git_verb(rest)
        if verb == "merge":
            return "merge"
        if verb in ("commit", "checkout", "switch", "worktree", "rebase", "reset", "push", "branch", "tag"):
            return "git"
        return "read"
    if p in READ_PROGRAMS:
        return "read"
    return "command"


def classify(cmd: str) -> str:
    kinds = {_kind(seg["argv"]) for seg in segments(cmd)}
    for k in ("run_stop", "merge", "run_start", "experiment", "dev", "git", "command"):
        if k in kinds:
            return k
    return "read"


def detached_pipeline(cmd: str) -> bool:
    """A pipeline program started in the background by hand (nohup / setsid / disown / trailing &)."""
    return any(seg["detached"] and runs_pipeline(seg["argv"]) for seg in segments(cmd))


# ------------------------------------------------------------------ repo / module resolution
def repo_of(path: str) -> tuple[Path | None, Path | None]:
    """(worktree root, main repo root) of a path; main differs from worktree for a linked git worktree."""
    p = Path(path)
    for d in (p, *p.parents) if p.is_dir() else p.parents:
        g = d / ".git"
        if g.is_dir():
            return d, d
        if g.is_file():                                     # linked worktree: "gitdir: <main>/.git/worktrees/<name>"
            try:
                gd = g.read_text().split(":", 1)[1].strip()
                main = Path(gd).parents[2] if "/worktrees/" in gd else d
            except (OSError, IndexError):
                main = d
            return d, main
    return None, None


def project_of(main: Path | None) -> dict:
    if main and (main / "reins.toml").is_file():
        try:
            return tomllib.loads((main / "reins.toml").read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return {}
    return {}


def module_of(cfg: dict, rel: str) -> str | None:
    for name, m in (cfg.get("modules") or {}).items():
        if any(fnmatch.fnmatch(rel, g) for g in m.get("files", [])):
            return name
    return None


# ------------------------------------------------------------------ recording
def _prefix_sha(transcript: str | None, n: int = 3) -> str | None:
    """Hash of the first n user/assistant messages: a fork starts with its parent's history. A brand-new session has
    fewer than n messages at SessionStart; its hash is filled in later (fill_prefixes)."""
    if not transcript or not Path(transcript).is_file():
        return None
    h = hashlib.sha256()
    k = 0
    try:
        with open(transcript, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") in ("user", "assistant") and not o.get("isMeta"):
                    h.update(json.dumps(o.get("message", {}).get("content"), sort_keys=True, default=str).encode())
                    k += 1
                    if k >= n:
                        break
    except OSError:
        return None
    return h.hexdigest() if k >= n else None


def fill_prefixes(con, limit: int = 100) -> None:
    """Sessions whose transcript was too short when first seen get their prefix hash now."""
    for r in con.execute("SELECT id, transcript FROM session WHERE prefix_sha IS NULL AND transcript IS NOT NULL"
                         " ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall():
        p = _prefix_sha(r["transcript"])
        if p:
            con.execute("UPDATE session SET prefix_sha=? WHERE id=?", (p, r["id"]))


def _ensure(con, sid: str, cwd: str | None, transcript: str | None) -> None:
    row = con.execute("SELECT id FROM session WHERE id=?", (sid,)).fetchone()
    if row:
        con.execute("UPDATE session SET last_seen=?, status='active', ended=NULL, cwd=COALESCE(?, cwd) WHERE id=?",
                    (now(), cwd, sid))
        return
    con.execute("INSERT INTO session (id, started, last_seen, status, cwd, transcript, role) VALUES (?,?,?, 'active', ?,?, 'analysis')",
                (sid, now(), now(), cwd, transcript))


def event(con, sid: str, kind: str, *, cwd: str | None = None, transcript: str | None = None, path: str | None = None,
          detail: str | None = None) -> dict:
    """Record one hook event; infer the session's binding from it. Returns what was inferred."""
    info = {"kind": kind}
    with tx(con):
        _ensure(con, sid, cwd, transcript)
        repo = wt = module = batch = None
        target = path or cwd
        if target:
            w, main = repo_of(target)
            if w:
                wt, repo = str(w), str(main)
                cfg = project_of(main)
                if cfg.get("project"):
                    con.execute("UPDATE session SET project=COALESCE(project, ?) WHERE id=?", (cfg["project"], sid))
                if path and kind == "edit":
                    try:
                        module = module_of(cfg, str(Path(path).resolve().relative_to(w)))
                    except ValueError:
                        module = None
        if detail:
            m = BATCH_IN.search(detail)
            batch = m.group(1) if m else None
        con.execute("INSERT INTO session_event (at, session_id, kind, repo, worktree, path, module, batch_id, detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", (now(), sid, kind, repo, wt, path, module, batch, (detail or "")[:400]))
        s = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
        upd = {}
        if kind == "edit":
            upd["n_edits"] = s["n_edits"] + 1
            lease = con.execute("SELECT resource FROM lease WHERE resource=? AND holder=?", (f"worktree:{wt}", sid)).fetchone() if wt else None
            ver = con.execute("SELECT version, module, about FROM module_version WHERE worktree=? AND status='candidate'", (wt,)).fetchone() if wt else None
            if s["role"] in (None, "analysis"):
                upd["role"] = "develop"
            if ver and lease and not s["version"]:
                upd.update(version=ver["version"], module=ver["module"], purpose=s["purpose"] or ver["about"])
            elif module and not s["module"]:
                upd["module"] = module
        elif kind in ("run_start", "run_stop", "experiment", "merge", "dev", "git", "command", "blocked"):
            upd["n_commands"] = s["n_commands"] + 1
            if kind in ("run_start", "run_stop") and s["role"] in (None, "analysis"):
                upd["role"] = "run"
            if kind == "experiment" and s["role"] in (None, "analysis"):
                upd["role"] = "experiment"
            if batch and not s["batch_id"]:
                upd["batch_id"] = batch
        if upd:
            con.execute(f"UPDATE session SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), sid))
        info.update(module=module, batch=batch, worktree=wt)
    return info


def start(con, sid: str, cwd: str | None, transcript: str | None, source: str = "startup") -> dict:
    """SessionStart: register, detect a fork, and return what the session should be told about itself."""
    with tx(con):
        _ensure(con, sid, cwd, transcript)
        con.execute("UPDATE session SET transcript=COALESCE(transcript, ?) WHERE id=?", (transcript, sid))
        fill_prefixes(con)
        pre = _prefix_sha(transcript)
        parent = None
        if pre:
            row = con.execute("SELECT id FROM session WHERE prefix_sha=? AND id<>? ORDER BY started LIMIT 1", (pre, sid)).fetchone()
            parent = row["id"] if row else None
            con.execute("UPDATE session SET prefix_sha=COALESCE(prefix_sha, ?), parent=COALESCE(parent, ?) WHERE id=?",
                        (pre, parent, sid))
        con.execute("INSERT INTO session_event (at, session_id, kind, worktree, detail) VALUES (?,?,?,?,?)",
                    (now(), sid, "start" if source == "startup" else "resume", cwd, f"source={source}" + (f"; forked from {parent}" if parent else "")))
    return briefing(con, sid)


def end(con, sid: str, reason: str = "") -> None:
    with tx(con):
        _ensure(con, sid, None, None)
        con.execute("UPDATE session SET status='ended', ended=? WHERE id=?", (now(), sid))
        con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?,?, 'end', ?)", (now(), sid, reason))


def bind(con, sid: str, role: str, purpose: str, module: str | None = None, version: str | None = None,
         batch: str | None = None) -> None:
    if role not in ("develop", "run", "experiment", "analysis"):
        raise ReinsError("role is develop | run | experiment | analysis")
    if not purpose.strip() or len(purpose.strip()) < 6:
        raise ReinsError("purpose: one short line saying what this session is for")
    with tx(con):
        _ensure(con, sid, None, None)
        if version:
            v = con.execute("SELECT module FROM module_version WHERE version=?", (version,)).fetchone()
            if not v:
                raise ReinsError(f"module version {version} is not registered")
            module = module or v["module"]
        if module and not con.execute("SELECT 1 FROM module WHERE name=?", (module,)).fetchone():
            raise ReinsError(f"module {module} is not registered")
        if batch and not con.execute("SELECT 1 FROM batch WHERE batch_id=?", (batch,)).fetchone():
            raise ReinsError(f"batch {batch} is not registered")
        con.execute("UPDATE session SET role=?, purpose=?, module=COALESCE(?, module), version=COALESCE(?, version),"
                    " batch_id=COALESCE(?, batch_id) WHERE id=?", (role, purpose.strip(), module, version, batch, sid))
        con.execute("INSERT INTO session_event (at, session_id, kind, module, batch_id, detail) VALUES (?,?, 'bind', ?,?,?)",
                    (now(), sid, module, batch, f"{role}: {purpose.strip()}"))


# ------------------------------------------------------------------ views
def _minutes(ts: str | None) -> float:
    return (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() / 60 if ts else 1e9


def refresh_status(con) -> None:
    with tx(con):
        for s in con.execute("SELECT id, last_seen FROM session WHERE status='active'").fetchall():
            if _minutes(s["last_seen"]) > IDLE_MIN:
                con.execute("UPDATE session SET status='idle' WHERE id=?", (s["id"],))


def owned(con, sid: str) -> dict:
    leases = [r[0] for r in con.execute("SELECT resource FROM lease WHERE holder=?", (sid,))]
    return {"batches": [l.split(":", 1)[1] for l in leases if l.startswith("batch:")],
            "worktrees": [l.split(":", 1)[1] for l in leases if l.startswith("worktree:")]}


def title(s) -> str:
    if s["purpose"]:
        return s["purpose"]
    if s["role"] == "develop" and s["module"]:
        return f"开发 {s['module']}（未登记版本）"
    if s["role"] in ("run", "experiment") and s["batch_id"]:
        return f"{'运行' if s['role'] == 'run' else '实验'} {s['batch_id']}"
    return {"develop": "开发（模块未识别）", "run": "运行", "experiment": "实验", "analysis": "分析 / 只读"}.get(s["role"] or "", "未知")


def timeline(con, sid: str, kinds: tuple[str, ...] | None = None, limit: int = 40) -> list[dict]:
    q = "SELECT * FROM session_event WHERE session_id=?"
    args: list = [sid]
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    return [dict(r) for r in con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def conflicts(con) -> list[dict]:
    """Conflicts between sessions that are not ended. Each: kind, sessions, what, detail."""
    refresh_status(con)
    live = {r["id"]: r for r in con.execute("SELECT * FROM session WHERE status<>'ended'")}
    since = (dt.datetime.now() - dt.timedelta(hours=CONFLICT_WINDOW_H)).isoformat(timespec="seconds")
    out = []
    edits = con.execute("SELECT session_id, worktree, repo, path, module FROM session_event WHERE kind='edit' AND at>?",
                        (since,)).fetchall()
    by_file, by_module = {}, {}
    for e in edits:
        if e["session_id"] not in live:
            continue
        rel = e["path"]
        if e["worktree"] and e["path"] and e["path"].startswith(e["worktree"]):
            rel = (e["repo"] or "") + "::" + e["path"][len(e["worktree"]):]
        by_file.setdefault(rel, set()).add(e["session_id"])
        if e["module"]:
            by_module.setdefault(((e["repo"] or ""), e["module"]), set()).add(e["session_id"])
        # editing main's worktree of a project, or a worktree another session holds
        if e["worktree"] and e["repo"] and e["worktree"] == e["repo"] and project_of(Path(e["repo"])).get("project"):
            out.append({"kind": "edits_main", "sessions": [e["session_id"]], "what": e["worktree"],
                        "detail": f"直接改了 main 的工作目录：{e['path']}"})
        held = con.execute("SELECT holder FROM lease WHERE resource=?", (f"worktree:{e['worktree']}",)).fetchone() if e["worktree"] else None
        if held and held["holder"] != e["session_id"]:
            out.append({"kind": "foreign_worktree", "sessions": [e["session_id"], held["holder"]], "what": e["worktree"],
                        "detail": f"改了别的会话持有的 worktree：{e['path']}"})
    for f, ss in by_file.items():
        if len(ss) > 1:
            out.append({"kind": "same_file", "sessions": sorted(ss), "what": f.split("::")[-1], "detail": "多个会话改了同一个文件"})
    for (repo, mod), ss in by_module.items():
        if len(ss) > 1:
            out.append({"kind": "same_module", "sessions": sorted(ss), "what": mod, "detail": f"多个会话在改同一个模块 {mod}"})
    # two live sessions bound to the same module as developers
    devs = {}
    for s in live.values():
        if s["role"] == "develop" and s["module"]:
            devs.setdefault(s["module"], set()).add(s["id"])
    for mod, ss in devs.items():
        if len(ss) > 1 and not any(c["kind"] == "same_module" and c["what"] == mod for c in out):
            out.append({"kind": "same_module", "sessions": sorted(ss), "what": mod, "detail": f"多个会话同时开发 {mod}"})
    # run actions on a batch held by another session
    for r in con.execute("SELECT session_id, batch_id, kind, detail FROM session_event WHERE kind IN ('run_start','run_stop','blocked')"
                         " AND batch_id IS NOT NULL AND at>?", (since,)).fetchall():
        held = con.execute("SELECT holder FROM lease WHERE resource=?", (f"batch:{r['batch_id']}",)).fetchone()
        if held and held["holder"] != r["session_id"] and r["session_id"] in live:
            out.append({"kind": "foreign_batch", "sessions": [r["session_id"], held["holder"]], "what": r["batch_id"],
                        "detail": f"对别的会话的批次做了 {r['kind']}：{(r['detail'] or '')[:120]}"})
    # several sessions starting runs at once (they share one GPU)
    runners = sorted({r["session_id"] for r in con.execute(
        "SELECT session_id FROM session_event WHERE kind='run_start' AND at>?",
        ((dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"),)) if r["session_id"] in live})
    if len(runners) > 1:
        out.append({"kind": "concurrent_runs", "sessions": runners, "what": "GPU / machine",
                    "detail": f"{len(runners)} 个会话在 2 小时内都启动了运行，可能抢 GPU"})
    seen, uniq = set(), []
    for c in out:
        k = (c["kind"], tuple(c["sessions"]), c["what"])
        if k not in seen:
            seen.add(k); uniq.append(c)
    return uniq


def list_(con, include_ended: bool = False) -> list[dict]:
    refresh_status(con)
    q = "SELECT * FROM session" + ("" if include_ended else " WHERE status<>'ended'") + " ORDER BY last_seen DESC"
    out = []
    for s in con.execute(q).fetchall():
        last = con.execute("SELECT at, kind, detail, path FROM session_event WHERE session_id=? AND kind NOT IN ('read')"
                           " ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
        out.append({**dict(s), "title": title(s), "owned": owned(con, s["id"]),
                    "last": dict(last) if last else None, "idle_min": round(_minutes(s["last_seen"]))})
    return out


def briefing(con, sid: str) -> str:
    """What a session is told when it starts: who it is, what it owns, who else is working on what."""
    s = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    own = owned(con, sid)
    lines = [f"[reins] You are session {sid[:8]}. Board: http://127.0.0.1:8791 . Rules: /env/code/ReinsLite/docs/WORKFLOW.md"]
    if s and s["parent"]:
        lines.append(f"[reins] You were FORKED from session {s['parent'][:8]}. You own nothing it owns: its batches and "
                     f"worktrees stay with it. To develop a fix, first run: reins dev start MODULE SUFFIX --about '...' "
                     f"--from-batch BATCH --cases ID,...  (or reins session bind --role ... --purpose ...).")
    lines.append(f"[reins] You own: batches {own['batches'] or 'none'}; worktrees {own['worktrees'] or 'none'}.")
    others = [o for o in list_(con) if o["id"] != sid and o["status"] == "active"]
    for o in others[:8]:
        lines.append(f"[reins] Other active session {o['id'][:8]}: {o['title']} (cwd {o['cwd']})")
    from . import issues
    open_issues = [i for i in issues.list_(con) if i["status"] == "open"]
    for i in open_issues[:5]:
        lines.append(f"[reins] Open issue #{i['id']} on {i['batch_id']}: {i['symptom'][:80]} ({len(i['cases'])} cases). "
                     f"To fix it: reins dev start MODULE SUFFIX --about '...' --issue {i['id']}")
    lines.append("[reins] Say what you are for once you know: reins session bind --role develop|run|experiment|analysis "
                 "--purpose '...' [--module M | --batch B]. Start runs with reins run; merge with reins dev finish.")
    return "\n".join(lines)


# ------------------------------------------------------------------ what the board shows (no session details)
def development(con) -> list[dict]:
    """One item per module under development: registered candidate versions, plus modules that sessions are editing
    without a registered version. Items carry conflicts as plain text; never session ids or paths."""
    refresh_status(con)
    conf = conflicts(con)
    items = {}
    for v in con.execute("SELECT v.*, m.about AS module_about FROM module_version v JOIN module m ON m.name=v.module"
                         " WHERE v.status='candidate'"):
        items[("v", v["version"])] = {"module": v["module"], "module_about": v["module_about"], "version": v["version"],
                                      "about": v["about"], "registered": True, "sessions": {v["session"]} - {None},
                                      "last": v["created"]}
    recent = (dt.datetime.now() - dt.timedelta(hours=48)).isoformat(timespec="seconds")
    for s in con.execute("SELECT * FROM session WHERE status<>'ended' AND role='develop'"
                         " AND (module IS NOT NULL OR version IS NOT NULL)"):
        last = con.execute("SELECT MAX(at) FROM session_event WHERE session_id=? AND kind='edit'", (s["id"],)).fetchone()[0]
        if not s["version"] and (not last or last < recent):
            continue
        key = next((k for k, it in items.items() if s["version"] and it["version"] == s["version"]), None)
        if key:
            items[key]["sessions"].add(s["id"]); items[key]["last"] = max(items[key]["last"] or "", last or ""); continue
        mod = s["module"] or "?"
        k = ("u", mod)
        it = items.setdefault(k, {"module": mod, "module_about": None, "version": None, "about": s["purpose"],
                                  "registered": False, "sessions": set(), "last": last})
        it["sessions"].add(s["id"])
        it["about"] = it["about"] or s["purpose"]
        it["last"] = max(it["last"] or "", last or "")
        if mod != "?":
            m = con.execute("SELECT about FROM module WHERE name=?", (mod,)).fetchone()
            it["module_about"] = m["about"] if m else None
    out = []
    for it in items.values():
        flags = []
        for c in conf:
            if not set(c["sessions"]) & it["sessions"]:
                continue
            if c["kind"] == "same_module":
                flags.append(f"另一项开发也在改 {c['what']}")
            elif c["kind"] == "same_file":
                flags.append(f"另一项开发也改了 {Path(c['what']).name}")
            elif c["kind"] == "edits_main":
                flags.append("直接改了 main，没有走自己的分支")
            elif c["kind"] == "foreign_worktree":
                flags.append("改到了另一项开发的分支")
        if len(it["sessions"]) > 1:
            flags.append(f"{len(it['sessions'])} 个会话同时在做")
        out.append({"module": it["module"], "module_about": it["module_about"], "version": it["version"],
                    "about": it["about"] or "（还没说明在改什么）", "registered": it["registered"],
                    "last": it["last"], "conflicts": sorted(set(flags))})
    return sorted(out, key=lambda x: x["last"] or "", reverse=True)


def unregistered_runs(con, hours: int = 12) -> list[dict]:
    """Runs and experiments sessions started without a registered batch, as plain run items."""
    since = (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    out = []
    for r in con.execute("SELECT e.at, e.kind, e.detail FROM session_event e JOIN session s ON s.id=e.session_id"
                         " WHERE e.kind IN ('run_start','experiment') AND e.batch_id IS NULL AND e.at>? AND s.status<>'ended'"
                         " AND e.detail NOT LIKE '%reins run%' ORDER BY e.id DESC LIMIT 10", (since,)):
        segs = [" ".join(sg["argv"]) for sg in segments(r["detail"] or "") if runs_pipeline(sg["argv"])]
        cmd = (segs[0] if segs else (r["detail"] or "").strip().splitlines()[0])[:140]
        out.append({"at": r["at"], "kind": "实验" if r["kind"] == "experiment" else "运行", "what": cmd})
    return out


# ------------------------------------------------------------------ provenance index
def _find_transcript(sid: str) -> str | None:
    """A session seen before the hook was installed: its transcript is ~/.claude/projects/<dir>/<id>.jsonl."""
    import glob
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    return hits[0] if hits else None


def _index(con, rows) -> list[dict]:
    by = {}
    for sid, at, kind in rows:
        if not sid:
            continue
        d = by.setdefault(sid, {"session": sid, "first": at, "last": at, "actions": set()})
        d["first"], d["last"] = min(d["first"], at), max(d["last"], at)
        d["actions"].add(kind)
    out = []
    for sid, d in by.items():
        s = con.execute("SELECT transcript, parent FROM session WHERE id=?", (sid,)).fetchone()
        tr = s["transcript"] if s and s["transcript"] else _find_transcript(sid)
        out.append({**d, "actions": sorted(d["actions"]), "transcript": tr,
                    "forked_from": s["parent"] if s else None})
    return sorted(out, key=lambda x: x["first"])


def index_for_batch(con, batch: str) -> list[dict]:
    """Which sessions opened, started, paused, stopped or otherwise acted on this batch."""
    rows = [(r[0], r[1], r[2]) for r in con.execute("SELECT session, at, event FROM batch_event WHERE batch_id=?", (batch,))]
    rows += [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT session_id, at, kind FROM session_event WHERE batch_id=? AND kind<>'read'", (batch,))]
    rows += [(r[0], r[1], "process") for r in con.execute("SELECT session, started FROM process WHERE batch_id=?", (batch,))]
    own = con.execute("SELECT owner_session, created FROM batch WHERE batch_id=?", (batch,)).fetchone()
    if own and own["owner_session"]:
        rows.append((own["owner_session"], own["created"], "owner"))   # for an adopted batch: the session that started it
    return _index(con, rows)


def index_for_module(con, name: str) -> list[dict]:
    """Which sessions created / released a version of this module (or this version), or edited its files."""
    if re.match(r"^[a-z][a-z0-9_]*-", name):
        rows = [(r[0], r[1], r[2]) for r in con.execute("SELECT session, at, event FROM module_event WHERE version=?", (name,))]
        mod = con.execute("SELECT module, worktree FROM module_version WHERE version=?", (name,)).fetchone()
        if mod and mod["worktree"]:
            rows += [(r[0], r[1], r[2]) for r in con.execute(
                "SELECT session_id, at, kind FROM session_event WHERE worktree=? AND kind IN ('edit','git','merge','dev')",
                (mod["worktree"],))]
        return _index(con, rows)
    rows = [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT e.session, e.at, e.event FROM module_event e JOIN module_version v ON v.version=e.version WHERE v.module=?", (name,))]
    rows += [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT session_id, at, kind FROM session_event WHERE module=? AND kind<>'read'", (name,))]
    return _index(con, rows)


def format_index(idx: list[dict]) -> str:
    if not idx:
        return "  来源会话：无记录"
    lines = ["  来源会话："]
    for d in idx:
        fork = f"，从 {d['forked_from'][:8]} fork" if d["forked_from"] else ""
        lines.append(f"    {d['session']}  {d['first'][:16]} → {d['last'][:16]}  {', '.join(d['actions'])}{fork}")
        if d["transcript"]:
            lines.append(f"      记录：{d['transcript']}")
    return "\n".join(lines)
