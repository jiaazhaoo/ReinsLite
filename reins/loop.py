"""The improvement loop, without a person pushing it: human verdicts in, misses out as issues, issues to a fixer.

User 2026-10-23 (refactor step 4): every link of "a person marks a case wrong -> an issue -> a development session
fixes it -> the gate -> resume" waited for someone. Now:

  1. import   the project's review databases ([review_source] in its reins.toml) are read every hour for the recent
              production / rework batches; new verdicts become review records (append-only, signed by the reviewer)
  2. misses   a case the newest run of a work package finished without a person (its result value is in [results]
              good) that a reviewer marked wrong is a miss; misses no open issue in that work package covers become
              one issue per error type, on the batch
  3. dispatch an issue can be handed to a headless agent: it starts a candidate for the issue (reins dev start ...
              --issue N), fixes it in that worktree and runs the project's gate until it is green. It never merges:
              `reins dev finish VERSION` is the one step a person takes ("采用"). Off unless config
              loop.auto_dispatch = true (it spends the agent subscription and writes code); `reins issue dispatch N`
              hands over one issue.

[review_source] (project reins.toml):
    databases = ["{work_dir}/lab/review/review.db", "/data/councils/{scope0}/review/**/review.db"]
    table = "events"
    where = "action = 'verdict'"
    columns = { case = "case_id", reviewer = "reviewer", verdict = "verdict", error_type = "error_type", note = "note", at = "at" }
    verdicts = { correct = "correct", error = "wrong", query = "unsure" }
    case_pattern = "([0-9]+)$"
    commands = ["python3 /env/code/ledger/feedback_events.py {scope0}"]   # each prints events as JSON lines:
                # {"case", "reviewer", "verdict": correct|wrong|unsure, "error_type", "note", "at"} (client feedback)
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from . import batches, issues, review
from .store import config, home

DAYS = 30                      # batches created within this many days are watched
EVERY_MIN = 60                 # the loop runs at most this often
_LAST: dict[str, float] = {}


def _cfg(project: str) -> dict:
    from . import production
    return production._cfg(project)


def _databases(spec: dict, b) -> list[Path]:
    from .paths import work_dir
    vals = {"work_dir": str(work_dir(b) or ""), "scope": b["scope"], "scope0": b["scope"].split("-")[0], "project": b["project"]}
    out = []
    for t in spec.get("databases") or []:
        try:
            pat = t.format(**vals)
        except (KeyError, IndexError):
            continue
        out += [Path(p) for p in glob.glob(pat, recursive=True) if "/temp/" not in p and "backup" not in p]
    return sorted(set(out))


def import_reviews(con, bid: str) -> int:
    """New verdicts on this batch's cases from the project's review databases; returns how many were recorded."""
    b = batches.get(con, bid)
    spec = _cfg(b["project"]).get("review_source") or {}
    if not spec:
        return 0
    cols = {"case": "case_id", "reviewer": "reviewer", "verdict": "verdict", "error_type": "error_type", "note": "note",
            "at": "at", **(spec.get("columns") or {})}
    words = spec.get("verdicts") or {}
    pat = re.compile(spec["case_pattern"]) if spec.get("case_pattern") else None
    members = {r[0] for r in con.execute("SELECT case_id FROM batch_case WHERE batch_id=?", (bid,))}
    seen = {tuple(r) for r in con.execute(
        "SELECT case_id, reviewer, at, verdict FROM review_event WHERE batch_id=? AND action='verdict'", (bid,))}
    events = []
    for db in _databases(spec, b):
        try:
            src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = src.execute(f"SELECT {cols['case']}, {cols['reviewer']}, {cols['verdict']}, {cols['error_type']}, "
                               f"{cols['note']}, {cols['at']} FROM {spec.get('table', 'events')}"
                               f" WHERE {spec.get('where', '1=1')} ORDER BY {cols['at']}").fetchall()
            src.close()
        except sqlite3.Error:
            continue
        for case, reviewer, verdict, et, note, at in rows:
            c = str(case or "")
            if pat:
                m = pat.search(c)
                c = m.group(1) if m else c
            v = words.get(str(verdict), verdict)
            if c not in members or v not in review.VERDICTS or not reviewer or (c, str(reviewer), at, v) in seen:
                continue
            seen.add((c, str(reviewer), at, v))
            events.append({"case_id": c, "reviewer": str(reviewer), "action": "verdict", "verdict": v, "at": at,
                           "error_type": (et or "unspecified") if v == "wrong" else et,
                           "note": note or (f"({verdict})" if v == "unsure" else None), "client": f"loop:{db}"[:200]})
    for cmd in spec.get("commands") or []:              # verdicts kept elsewhere (client feedback), as JSON lines
        try:
            line_cmd = cmd                               # only the known placeholders: a command may hold JSON braces
            for k_, v_ in {"work_dir": str(_wd(b)), "scope": b["scope"], "scope0": b["scope"].split("-")[0],
                           "project": b["project"], "batch": bid}.items():
                line_cmd = line_cmd.replace("{" + k_ + "}", v_)
            out = subprocess.run(line_cmd, shell=True, capture_output=True, text=True, timeout=600).stdout
        except (KeyError, IndexError, subprocess.TimeoutExpired, OSError):
            continue
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            c, v, rv, at = str(d.get("case") or ""), d.get("verdict"), str(d.get("reviewer") or ""), d.get("at")
            if pat:
                m = pat.search(c)
                c = m.group(1) if m else c
            if c not in members or v not in review.VERDICTS or not rv or (c, rv, at, v) in seen:
                continue
            seen.add((c, rv, at, v))
            events.append({"case_id": c, "reviewer": rv, "action": "verdict", "verdict": v, "at": at,
                           "error_type": (d.get("error_type") or "unspecified") if v == "wrong" else d.get("error_type"),
                           "note": d.get("note") or (f"({rv})" if v == "unsure" else None), "client": f"loop:{line_cmd}"[:200]})
    if events:
        review.record(con, bid, events, review.taxonomy(None))
    return len(events)


def _wd(b):
    from .paths import work_dir
    return work_dir(b) or ""


def misses(con, bid: str) -> dict[str, list[str]]:
    """{error_type: cases} the run finished without a person that a reviewer marked wrong (latest verdict per reviewer)."""
    from . import production
    from .paths import work_dir
    b = batches.get(con, bid)
    spec = _cfg(b["project"]).get("results") or {}
    wd = work_dir(b)
    if not spec.get("file") or not wd:
        return {}
    res = production._results(Path(wd) / spec["file"], spec)
    good = set(spec.get("good") or [])
    out: dict[str, set] = {}
    for (case, _t, rev), v in review.current_verdicts(con, bid).items():
        if v["verdict"] == "wrong" and case in res and res[case][0] in good:
            who = rev.split(":", 1)[1] if rev.startswith("client:") else "reviewer"
            out.setdefault(f"{who}|{v['error_type'] or 'unspecified'}", set()).add(case)
    return {k: sorted(v) for k, v in out.items()}


def open_issues(con, bid: str) -> list[int]:
    """One issue per error type for misses no open issue in the same work package (scope) covers yet: a miss is the
    case's problem, not one run's -- reruns of the same work do not open it again."""
    scope = batches.get(con, bid)["scope"]
    covered = set()
    for r in con.execute("SELECT i.cases FROM issue i JOIN batch b ON b.batch_id=i.batch_id WHERE b.scope=? AND i.status<>'closed'",
                         (scope,)):
        covered |= set(json.loads(r[0]))
    made = []
    for key, cases in sorted(misses(con, bid).items()):
        who, et = key.split("|", 1)
        new = [c for c in cases if c not in covered]
        if new:
            by = "a reviewer" if who == "reviewer" else f"the client ({who})"
            made.append(issues.open_(con, bid, new, f"finished without a person but marked wrong by {by}: "
                                                    f"{len(new)} cases, {et}", "check", et))
            covered |= set(new)
    return made


def tend(con, force: bool = False) -> list[str]:
    """One pass (at most hourly): import verdicts, open issues for misses, dispatch when allowed."""
    if not force and time.time() - _LAST.get("tend", 0) < EVERY_MIN * 60:
        return []
    _LAST["tend"] = time.time()
    since = (dt.datetime.now() - dt.timedelta(days=DAYS)).isoformat(timespec="seconds")
    out = []
    newest = {}                                          # the newest production / rework run of each work package
    for b in con.execute("SELECT batch_id, project, scope FROM batch WHERE type IN ('production','rework') AND created>?"
                         " AND status<>'closed' ORDER BY created", (since,)).fetchall():
        newest[(b["project"], b["scope"])] = b
    for b in newest.values():
        if not _cfg(b["project"]).get("review_source"):
            continue
        try:
            n = import_reviews(con, b["batch_id"])
            made = open_issues(con, b["batch_id"])
        except Exception as e:                                                  # noqa: BLE001 -- one batch never stops the loop
            out.append(f"{b['batch_id']}: loop skipped: {e}"[:300])
            continue
        if n:
            out.append(f"{b['batch_id']}: {n} new verdicts imported")
        for iid in made:
            out.append(f"{b['batch_id']}: issue #{iid} opened from reviewer verdicts")
            if (config().get("loop") or {}).get("auto_dispatch"):
                try:
                    out.append(dispatch(con, iid))
                except Exception as e:                                          # noqa: BLE001
                    out.append(f"issue #{iid}: not dispatched: {e}"[:300])
    return out


def dispatch(con, iid: int) -> str:
    """Hand an issue to a headless agent in the project's repo. It starts a candidate for the issue, fixes it there and
    runs the project's gate until green; it never merges (reins dev finish is the person's step)."""
    import shutil
    import subprocess
    row = issues.get(con, iid)
    if not row["batch_id"]:
        raise RuntimeError("only issues on a batch are dispatched (they name the cases a fix is checked on)")
    b = batches.get(con, row["batch_id"])
    cfg = _cfg(b["project"])
    repo, gate = (cfg.get("dev") or {}).get("repo"), (cfg.get("dev") or {}).get("gate")
    if not repo or not gate:
        raise RuntimeError(f"project {b['project']} has no [dev] repo / gate")
    agent = ((config().get("loop") or {}).get("agent_cmd") or "claude -p").split()
    if not shutil.which(agent[0]):
        raise RuntimeError(f"no agent command {agent[0]!r} on PATH")
    prompt = (f"Fix reins issue #{iid} of project {b['project']}: {row['symptom']}. Batch {row['batch_id']}, cases "
              f"{', '.join(json.loads(row['cases'])[:40])}. Read the repo's CLAUDE.md and reins.toml first. Start a "
              f"candidate: reins dev start <module> fix_issue{iid} --about '<what you change>' --issue {iid}, then work "
              f"only in the worktree it prints. Run the gate there until it is green: {gate}. Do NOT run reins dev finish "
              f"(merging is the user's step). End with one line: the candidate version, the gate result, and what changed.")
    logd = home() / "loop"
    logd.mkdir(parents=True, exist_ok=True)
    log = open(logd / f"issue-{iid}.log", "ab")
    p = subprocess.Popen([*agent, prompt], cwd=repo, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    issues._ev(con, iid, "dispatched", f"headless agent pid {p.pid} in {repo}; log {logd / f'issue-{iid}.log'}")
    con.commit()
    return f"issue #{iid}: dispatched to a headless agent (pid {p.pid}) in {repo}"
