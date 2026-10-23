"""State invariants: what must be true of the registry at every moment, checked instead of assumed.

User 2026-10-23: "怎么老是出现状态很奇怪的情况，似乎状态就没有正确过". An audit that day found 60 of 148 done batches
with a stage marked done while cases had no outcome: batch and stage states are set by 26 UPDATE statements in 7
modules and only the normal stage end checks conservation. Until the state is derived in one place, these checks say
where it is wrong. The user does not handle them (user 2026-10-23: "我要看那么多错误干嘛，我又不会处理这些错误"): the
watchdog runs them every pass and REPAIRS what a fixed rule can repair (each repair recorded on the batch); what is still
wrong after ESCALATE passes becomes an issue on the module that owns it, for a development session. Nothing here goes
to the user's board. `reins check state` lists the current findings for a developer.

Repairs (repair()):
  conservation  a run that records no per-case outcome at all (not production / rework / intake): per_case = 0, its
                counts are shown as "not recorded per case"; otherwise the missing cases of a done stage are marked
                skipped, reason "no outcome recorded by the run"
  process       a process row whose process is gone: state lost (the batch's own pause rule then applies)
  stage_order   a done batch's unfinished stages: skipped, "the batch ended before this stage ran"
  visible       not repairable by rule: a board defect -> issue on module board

  conservation    a stage marked done: every case of the batch has an outcome there (done / skipped / failed)
  process         'running' has a live process; a live process belongs to a live batch; a process row that says
                  running has its process
  stage_order     at most one stage running; a done batch has no unfinished stage; a running batch is in a stage or
                  has its process (a self-staged run between two stages)
  visible         every batch that is not finished (open / running / paused) is on the board's run list
"""
from __future__ import annotations

import json

from .store import now, tx

LIVE = ("open", "running", "paused")
STRICT = ("production", "rework", "intake")      # runs whose every case must be accounted for
ESCALATE = 3                                     # passes a violation may survive the repairs before it becomes an issue
REPAIRED = "no outcome recorded by the run (repaired by the state check)"
CHECKS = ("conservation", "process", "stage_order", "visible")


def _alive(p) -> bool:
    from . import runner
    return bool((p["pgid"] and runner._alive(p["pgid"])) or (p["supervisor_pid"] and runner._alive(p["supervisor_pid"])))


def conservation(con) -> list[dict]:
    out = []
    for b in con.execute("SELECT batch_id, n_cases FROM batch WHERE status<>'closed' AND n_cases>0 AND COALESCE(per_case,1)=1"):
        short = []
        for st, have in con.execute(
                "SELECT s.stage, (SELECT COUNT(*) FROM case_current c WHERE c.batch_id=s.batch_id AND c.stage=s.stage"
                " AND c.status IN ('done','skipped','failed')) FROM batch_stage s WHERE s.batch_id=? AND s.status='done'"
                " ORDER BY s.ord", (b["batch_id"],)):
            if have < b["n_cases"]:
                short.append(f"{st} {have}/{b['n_cases']}")
        if short:
            out.append({"check": "conservation", "batch": b["batch_id"],
                        "detail": "stage done, cases without an outcome: " + ", ".join(short)})
    return out


def process(con) -> list[dict]:
    out = []
    procs: dict[str, list] = {}
    for p in con.execute("SELECT * FROM process WHERE state IN ('running','paused')"):
        procs.setdefault(p["batch_id"], []).append(p)
    for bid, ps in procs.items():
        for p in ps:
            if not _alive(p):
                out.append({"check": "process", "batch": bid,
                            "detail": f"process #{p['id']} recorded as {p['state']}, but pgid {p['pgid']} is gone"})
    for b in con.execute("SELECT batch_id, status FROM batch"):
        live = [p for p in procs.get(b["batch_id"], []) if _alive(p)]
        if b["status"] == "running" and not live:
            out.append({"check": "process", "batch": b["batch_id"], "detail": "batch running, no live process"})
        if b["status"] in ("done", "closed", "failed") and live:
            out.append({"check": "process", "batch": b["batch_id"],
                        "detail": f"batch {b['status']}, process #{live[0]['id']} still alive"})
    return out


def stage_order(con) -> list[dict]:
    out = []
    for b in con.execute("SELECT batch_id, status FROM batch WHERE status<>'closed'"):
        st = [r[0] for r in con.execute("SELECT status FROM batch_stage WHERE batch_id=? ORDER BY ord", (b["batch_id"],))]
        if st.count("running") > 1:
            out.append({"check": "stage_order", "batch": b["batch_id"], "detail": f"{st.count('running')} stages running at once"})
        if b["status"] == "done" and any(s not in ("done", "skipped") for s in st):
            out.append({"check": "stage_order", "batch": b["batch_id"],
                        "detail": "batch done, stage(s) not finished: " + ", ".join(sorted({s for s in st if s not in ('done', 'skipped')}))})
        if b["status"] == "running" and "running" not in st:
            alive = any(_alive(p) for p in con.execute("SELECT * FROM process WHERE batch_id=? AND state IN ('running','paused')",
                                                         (b["batch_id"],)))
            if not alive:
                out.append({"check": "stage_order", "batch": b["batch_id"], "detail": "batch running, no stage running and no process"})
    return out


def visible(con) -> list[dict]:
    from . import board
    shown = {r["batch_id"] for r in board.running_cards(con)}
    return [{"check": "visible", "batch": b["batch_id"], "detail": f"batch {b['status']} is not on the board's run list"}
            for b in con.execute(f"SELECT batch_id, status FROM batch WHERE status IN ({','.join('?' * len(LIVE))})", LIVE)
            if b["batch_id"] not in shown]


def run(con, only: list[str] | None = None) -> list[dict]:
    fns = {"conservation": conservation, "process": process, "stage_order": stage_order, "visible": visible}
    out = []
    for name in only or CHECKS:
        try:
            out += fns[name](con)
        except Exception as e:                                                   # noqa: BLE001 -- a broken check is a finding
            out.append({"check": name, "batch": None, "detail": f"the check itself failed: {type(e).__name__}: {e}"})
    return out


def repair(con, items: list[dict]) -> list[str]:
    """Clear what a fixed rule can clear; returns one line per repair (each also an event on its batch)."""
    from . import batches
    done = []
    for i in items:
        bid, chk = i["batch"], i["check"]
        if not bid:
            continue
        try:
            b = batches.get(con, bid)
            if b["status"] not in ("done", "failed") and chk != "process":
                continue                                 # a live batch is its owner's: escalated if it stays wrong
            if chk == "conservation":
                outcomes = con.execute("SELECT COUNT(*) FROM case_event WHERE batch_id=? AND status<>'started'", (bid,)).fetchone()[0]
                if b["type"] not in STRICT and not outcomes:
                    with tx(con):
                        con.execute("UPDATE batch SET per_case=0 WHERE batch_id=?", (bid,))
                        batches._event(con, bid, "per_case_off", "the run recorded no per-case outcome; its counts are not "
                                                                 "per case (state check)")
                    done.append(f"{bid}: not recorded per case")
                    continue
                from . import lineage
                with_result = lineage.results_of(con, b)        # cases that reached the end: done in every stage
                for (stage,) in con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status='done'", (bid,)).fetchall():
                    missing = [r[0] for r in con.execute(
                        "SELECT c.case_id FROM batch_case c LEFT JOIN case_current e ON e.batch_id=c.batch_id AND e.stage=?"
                        " AND e.case_id=c.case_id WHERE c.batch_id=? AND (e.status IS NULL OR e.status='started')", (stage, bid))]
                    if not missing:
                        continue
                    ok = [c for c in missing if c in with_result]
                    rest = [c for c in missing if c not in with_result]
                    t = now()
                    with tx(con):
                        con.executemany("INSERT INTO case_event (at, batch_id, stage, case_id, status, reason) VALUES (?,?,?,?,?,?)",
                                        [(t, bid, stage, c, "done", "has a result in the run's results file (repaired by the state check)") for c in ok]
                                        + [(t, bid, stage, c, "skipped", REPAIRED) for c in rest])
                        batches._event(con, bid, "outcomes_repaired", f"{stage}: {len(ok)} done from the results file, "
                                                                      f"{len(rest)} skipped ({REPAIRED})")
                    done.append(f"{bid}/{stage}: {len(ok)} done from the results file, {len(rest)} skipped")
            elif chk == "process" and "recorded as" in i["detail"]:
                with tx(con):
                    n = con.execute("UPDATE process SET state='lost', ended=COALESCE(ended, ?) WHERE batch_id=? AND"
                                    " state IN ('running','paused') AND id=?", (now(), bid, int(i["detail"].split("#")[1].split()[0]))).rowcount
                    if n:
                        batches._event(con, bid, "process_lost", i["detail"] + " (state check)")
                if n:
                    done.append(f"{bid}: process marked lost")
            elif chk == "stage_order" and i["detail"].startswith("batch done, stage(s) not finished"):
                with tx(con):
                    n = con.execute("UPDATE batch_stage SET status='skipped', ended=COALESCE(ended, ?), note=? WHERE batch_id=?"
                                    " AND status NOT IN ('done','skipped')",
                                    (now(), "the batch ended before this stage ran (state check)", bid)).rowcount
                    batches._event(con, bid, "stages_skipped", f"{n} unfinished stage(s) of a done batch (state check)")
                done.append(f"{bid}: {n} stage(s) skipped")
        except Exception as e:                                                  # noqa: BLE001 -- left for escalation
            done.append(f"{bid}: repair failed: {type(e).__name__}: {e}")
    return done


def escalate(con, items: list[dict]) -> list[int]:
    """Violations still there after the repairs, pass after pass: an issue on the module that owns them (once)."""
    from . import issues
    keys = {f"{i['check']}|{i['batch'] or ''}|{i['detail']}": i for i in items}
    t, opened = now(), []
    with tx(con):
        con.execute(f"DELETE FROM invariant_seen WHERE key NOT IN ({','.join('?' * len(keys)) or 'NULL'})", list(keys))
        for k in keys:
            con.execute("INSERT INTO invariant_seen (key, first_at, passes) VALUES (?,?,1)"
                        " ON CONFLICT(key) DO UPDATE SET passes=passes+1", (k, t))
    for k, i in keys.items():
        row = con.execute("SELECT passes, issue_id FROM invariant_seen WHERE key=?", (k,)).fetchone()
        if row["passes"] >= ESCALATE and not row["issue_id"]:
            module = "board" if i["check"] == "visible" else "core"
            symptom = (f"state check ({i['check']}) not cleared by the repairs for {row['passes']} passes: "
                       f"{i['batch'] or '-'} -- {i['detail']}")
            try:                                         # the framework module that owns it, else the batch itself
                n = issues.open_(con, None, [], symptom, module=module)
            except Exception:                            # noqa: BLE001 -- a registry without that module
                first = con.execute("SELECT case_id FROM batch_case WHERE batch_id=? LIMIT 1", (i["batch"] or "",)).fetchone()
                if not first:
                    continue
                n = issues.open_(con, i["batch"], [first[0]], symptom)
            with tx(con):
                con.execute("UPDATE invariant_seen SET issue_id=? WHERE key=?", (n, k))
            opened.append(n)
    return opened


def record(con, items: list[dict]) -> None:
    key = sorted((i["check"], i["batch"] or "", i["detail"]) for i in items)
    last = con.execute("SELECT items FROM invariant_run ORDER BY id DESC LIMIT 1").fetchone()
    before = sorted((i["check"], i["batch"] or "", i["detail"]) for i in json.loads(last[0])) if last else None
    if key != before:
        with tx(con):
            con.execute("INSERT INTO invariant_run (at, n, items) VALUES (?,?,?)", (now(), len(items), json.dumps(items)))


def tend(con) -> dict:
    """One watchdog pass: check, repair, check again, record, escalate what is left."""
    found = run(con)
    fixed = repair(con, found) if found else []
    left = run(con) if fixed else found
    record(con, left)
    return {"found": len(found), "repaired": fixed, "left": len(left), "issues": escalate(con, left)}


def latest(con) -> dict:
    r = con.execute("SELECT at, n, items FROM invariant_run ORDER BY id DESC LIMIT 1").fetchone()
    return {"at": r[0], "n": r[1], "items": json.loads(r[2])} if r else {"at": None, "n": None, "items": []}
