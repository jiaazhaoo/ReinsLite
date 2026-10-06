"""C3 batches + C1 case ledger. A batch is registered before it runs; every stage accounts for every case."""
from __future__ import annotations

import json
from pathlib import Path

from . import leases, modules, names
from .store import ReinsError, now, session, today, tx

STRICT_TYPES = ("production", "rework")       # must run released module versions; may auto-restart
OUTCOMES = ("done", "skipped", "failed")
CASE_STATUSES = ("started", *OUTCOMES)


def _event(con, batch: str, event: str, detail: str | None = None) -> None:
    con.execute("INSERT INTO batch_event (at, batch_id, event, detail, session) VALUES (?,?,?,?,?)",
                (now(), batch, event, detail, session()))


def get(con, batch: str):
    names.parse_batch_id(batch)
    row = con.execute("SELECT * FROM batch WHERE batch_id=?", (batch,)).fetchone()
    if not row:
        raise ReinsError(f"batch {batch!r} is not registered")
    return row


def _own(con, batch: str) -> None:
    """Operational actions on a batch come from its owner session (a fork is a different session)."""
    leases.check(con, f"batch:{batch}")


def parse_stage_spec(spec: str) -> dict:
    """NAME[=MODULE_VERSION][:paid][:cap=1.5][:limit=600s]"""
    head, *opts = spec.split(":")
    stage, _, mv = head.partition("=")
    out = {"stage": stage, "module_version": mv or None, "paid": 0, "spend_cap": None, "time_limit_s": None}
    for o in opts:
        if o == "paid":
            out["paid"] = 1
        elif o.startswith("cap="):
            out["spend_cap"] = float(o[4:]); out["paid"] = 1
        elif o.startswith("limit="):
            out["time_limit_s"] = int(o[6:].rstrip("s"))
        else:
            raise ReinsError(f"stage spec {spec!r}: unknown option {o!r} (paid | cap=USD | limit=SECONDS)")
    return out


def open_(con, *, project: str, council: str, wp: str, type_: str, purpose: str, case_file: Path,
          stages: list[dict], parent: str | None = None, release_name: str | None = None,
          aliases: list[str] | None = None, case_pattern: str = names.DEFAULT_CASE_PATTERN,
          work_dir: str | None = None, spend_cap: float = 0.0, day: str | None = None) -> str:
    if not purpose.strip():
        raise ReinsError("a batch needs a purpose (one sentence: why it runs)")
    if not stages:
        raise ReinsError("a batch needs at least one planned stage")
    ids = names.read_case_set(case_file)
    problems = names.case_problems(ids, case_pattern)
    if problems:
        shown = "\n  ".join(problems[:20]) + (f"\n  ... {len(problems) - 20} more" if len(problems) > 20 else "")
        raise ReinsError(f"case set {case_file} is not usable ({len(problems)} problems):\n  {shown}")
    if work_dir and (Path(work_dir).resolve() == Path("/env/code") or Path("/env/code") in Path(work_dir).resolve().parents):
        raise ReinsError(f"work_dir {work_dir} is inside /env/code: batch outputs go under /data")
    day = day or today()
    with tx(con):
        if parent:
            get(con, parent)
        if release_name and not con.execute("SELECT 1 FROM release WHERE name=?", (release_name,)).fetchone():
            raise ReinsError(f"release {release_name!r} is not registered (reins dev release)")
        if type_ in STRICT_TYPES and not release_name:
            raise ReinsError(f"{type_} batches run from a release: pass --release")
        for s in stages:
            names.check_token("stage", s["stage"])
            mv = s["module_version"]
            if mv:
                row = modules.get(con, mv)
                if type_ in STRICT_TYPES and row["status"] != "released":
                    raise ReinsError(f"{type_} batches run released versions only; {mv} is {row['status']}")
            elif type_ in STRICT_TYPES:
                raise ReinsError(f"{type_} batch: stage {s['stage']!r} needs a module version (stage=<module_version>)")
            if s["spend_cap"] and spend_cap and s["spend_cap"] > spend_cap:
                raise ReinsError(f"stage {s['stage']} cap {s['spend_cap']} exceeds the batch cap {spend_cap}")
        seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM batch WHERE council=? AND wp=? AND type=? AND day=?",
                          (council, wp, type_, day)).fetchone()[0]
        bid = names.batch_id(council, wp, type_, day, seq)
        con.execute("INSERT INTO batch (batch_id, project, council, wp, type, day, seq, purpose, parent, case_set_path,"
                    " case_set_sha, n_cases, release_name, aliases, status, spend_cap, work_dir, owner_session, created)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?)",
                    (bid, project, council, wp, type_, day, seq, purpose, parent, str(case_file.resolve()),
                     names.sha256_lines(ids), len(ids), release_name, json.dumps(aliases or [], ensure_ascii=False),
                     spend_cap, work_dir, session(), now()))
        con.executemany("INSERT INTO batch_case VALUES (?,?)", [(bid, i) for i in ids])
        con.executemany("INSERT INTO batch_stage (batch_id, stage, ord, module_version, status, paid, spend_cap,"
                        " time_limit_s) VALUES (?,?,?,?, 'planned', ?,?,?)",
                        [(bid, s["stage"], k, s["module_version"], s["paid"], s["spend_cap"], s["time_limit_s"])
                         for k, s in enumerate(stages)])
        _event(con, bid, "opened", purpose)
    if session():
        leases.acquire(con, f"batch:{bid}", "owner")
    return bid


def approve_spend(con, batch: str, cap: float, who: str = "user") -> None:
    """C7/C9: paid stages run only after a human sets the cap. Recorded as an event."""
    if cap < 0:
        raise ReinsError("cap must be >= 0")
    with tx(con):
        b = get(con, batch)
        _live(b)
        con.execute("UPDATE batch SET spend_cap=? WHERE batch_id=?", (cap, batch))
        _event(con, batch, "spend_approved", f"${cap:.2f} by {who} (was ${b['spend_cap']:.2f})")


def _stage(con, batch: str, stage: str):
    row = con.execute("SELECT * FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()
    if not row:
        raise ReinsError(f"stage {stage!r} is not planned in {batch}")
    return row


def _live(b) -> None:
    if b["status"] in ("done", "failed", "closed"):
        raise ReinsError(f"batch {b['batch_id']} is {b['status']}; open a new batch (its outputs are frozen)")


def stage_start(con, batch: str, stage: str, module_version: str | None = None, output_path: str | None = None,
                log_path: str | None = None) -> list[str]:
    """Returns warnings. A stage that runs a different version than planned marks the batch mixed_version."""
    warnings = []
    with tx(con):
        b = get(con, batch)
        _live(b)
        _own(con, batch)
        st = _stage(con, batch, stage)
        if st["status"] in ("done", "skipped"):
            raise ReinsError(f"stage {stage} is already {st['status']}")
        if st["paid"] and b["spend_cap"] <= 0:
            raise ReinsError(f"stage {stage} calls paid models and the batch cap is $0: "
                             f"reins batch approve {batch} --cap USD (after the user agrees)")
        prev = con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND ord<? AND status NOT IN ('done','skipped')"
                           " ORDER BY ord", (batch, st["ord"])).fetchall()
        if prev:
            raise ReinsError(f"earlier stages not finished: {', '.join(r[0] for r in prev)} "
                             f"(a stage starts only when every case is accounted for upstream)")
        mv = module_version or st["module_version"]
        if mv:
            row = modules.get(con, mv)
            if b["type"] in STRICT_TYPES and row["status"] != "released":
                raise ReinsError(f"{b['type']} batches run released versions only; {mv} is {row['status']}")
        if st["module_version"] and mv != st["module_version"]:
            con.execute("UPDATE batch SET mixed_version=1 WHERE batch_id=?", (batch,))
            warnings.append(f"MIXED VERSION: stage {stage} planned {st['module_version']}, now runs {mv}")
            _event(con, batch, "mixed_version", f"{stage}: {st['module_version']} -> {mv}")
        con.execute("UPDATE batch_stage SET status='running', module_version=?, started=COALESCE(started, ?),"
                    " output_path=COALESCE(?, output_path), log_path=COALESCE(?, log_path) WHERE batch_id=? AND stage=?",
                    (mv, now(), output_path, log_path, batch, stage))
        con.execute("UPDATE batch SET status='running', status_reason=NULL WHERE batch_id=? AND status IN ('open', 'paused')",
                    (batch,))
        _event(con, batch, "stage_start", f"{stage} {mv or ''}".strip())
    return warnings


def mark(con, batch: str, stage: str, rows: list[tuple[str, str, str | None]]) -> int:
    """Record per-case events: (oachargeid, status, reason). All or nothing."""
    with tx(con):
        b = get(con, batch)
        _live(b)
        st = _stage(con, batch, stage)
        if st["status"] != "running":
            raise ReinsError(f"stage {stage} is {st['status']}; run: reins batch stage-start {batch} {stage}")
        members = {r[0] for r in con.execute("SELECT oachargeid FROM batch_case WHERE batch_id=?", (batch,))}
        seen, errors = set(), []
        for i, (cid, status, reason) in enumerate(rows, 1):
            if cid not in members:
                errors.append(f"row {i}: {cid!r} is not in this batch's case set")
            if cid in seen:
                errors.append(f"row {i}: {cid!r} appears twice")
            seen.add(cid)
            if status not in CASE_STATUSES:
                errors.append(f"row {i}: status {status!r} is not one of {', '.join(CASE_STATUSES)}")
            elif status in ("skipped", "failed") and not (reason or "").strip():
                errors.append(f"row {i}: {status} needs a reason")
        if errors:
            raise ReinsError(f"{len(errors)} problems, nothing recorded:\n  " + "\n  ".join(errors[:20]))
        t = now()
        con.executemany("INSERT INTO case_event (at, batch_id, stage, oachargeid, status, reason, module_version)"
                        " VALUES (?,?,?,?,?,?,?)", [(t, batch, stage, c, s, r, st["module_version"]) for c, s, r in rows])
    return len(rows)


def read_marks(path: Path) -> list[tuple[str, str, str | None]]:
    """TSV with header: oachargeid, status[, reason]."""
    import csv
    with path.open(encoding="utf-8-sig") as f:
        rd = csv.DictReader(f, delimiter="\t")
        need = {"oachargeid", "status"}
        if not need <= set(rd.fieldnames or []):
            raise ReinsError(f"{path}: needs columns oachargeid, status[, reason]; has {rd.fieldnames}")
        return [(r["oachargeid"], r["status"], r.get("reason") or None) for r in rd]


def ledger(con, batch: str, stage: str) -> dict:
    """C1 conservation for one stage: every case has exactly one current outcome."""
    _stage(con, batch, stage)
    n = con.execute("SELECT n_cases FROM batch WHERE batch_id=?", (batch,)).fetchone()[0]
    counts = dict(con.execute("SELECT status, COUNT(*) FROM case_current WHERE batch_id=? AND stage=? GROUP BY status",
                              (batch, stage)).fetchall())
    missing = [r[0] for r in con.execute(
        "SELECT c.oachargeid FROM batch_case c LEFT JOIN case_current e"
        " ON e.batch_id=c.batch_id AND e.stage=? AND e.oachargeid=c.oachargeid"
        " WHERE c.batch_id=? AND (e.status IS NULL OR e.status='started') ORDER BY c.oachargeid",
        (stage, batch))]
    return {"n_cases": n, "done": counts.get("done", 0), "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0), "running": counts.get("started", 0), "missing": missing}


def stage_end(con, batch: str, stage: str) -> dict:
    """Close a stage. Refused while any case has no outcome; failed cases are allowed but counted."""
    with tx(con):
        b = get(con, batch)
        _live(b)
        _own(con, batch)
        st = _stage(con, batch, stage)
        if st["status"] != "running":
            raise ReinsError(f"stage {stage} is {st['status']}, not running")
        led = ledger(con, batch, stage)
        if led["missing"]:
            raise ReinsError(f"stage {stage} cannot end: {len(led['missing'])} of {led['n_cases']} cases have no "
                             f"outcome (first: {', '.join(led['missing'][:10])}). Mark them done/skipped/failed.")
        con.execute("UPDATE batch_stage SET status='done', ended=? WHERE batch_id=? AND stage=?", (now(), batch, stage))
        _event(con, batch, "stage_end", f"{stage} done={led['done']} skipped={led['skipped']} failed={led['failed']}")
        _maybe_done(con, batch)
    return led


def _maybe_done(con, batch: str) -> None:
    left = con.execute("SELECT COUNT(*) FROM batch_stage WHERE batch_id=? AND status NOT IN ('done','skipped')",
                       (batch,)).fetchone()[0]
    if left == 0:
        con.execute("UPDATE batch SET status='done' WHERE batch_id=?", (batch,))
        _event(con, batch, "done", "all stages finished")


def skip_stage(con, batch: str, stage: str, why: str) -> None:
    if not why.strip():
        raise ReinsError("skipping a planned stage needs a reason")
    with tx(con):
        _live(get(con, batch))
        _own(con, batch)
        if _stage(con, batch, stage)["status"] != "planned":
            raise ReinsError(f"stage {stage} already started; it cannot be skipped")
        con.execute("UPDATE batch_stage SET status='skipped', note=? WHERE batch_id=? AND stage=?", (why, batch, stage))
        _event(con, batch, "stage_skipped", f"{stage}: {why}")
        _maybe_done(con, batch)


def set_status(con, batch: str, status: str, why: str, system: bool = False) -> None:
    """pause / resume / fail. `system=True` is the watchdog or runner acting, not a session."""
    allowed = {"paused": ("running", "open"), "running": ("paused",), "failed": ("open", "running", "paused")}
    with tx(con):
        b = get(con, batch)
        if not system:
            _own(con, batch)
        if b["status"] not in allowed[status]:
            raise ReinsError(f"batch is {b['status']}; cannot become {status}")
        con.execute("UPDATE batch SET status=?, status_reason=? WHERE batch_id=?", (status, why, batch))
        _event(con, batch, status, why)


def close(con, batch: str) -> None:
    with tx(con):
        b = get(con, batch)
        if b["status"] == "closed":
            raise ReinsError("already closed")
        _own(con, batch)
        open_stages = [r["stage"] for r in con.execute(
            "SELECT stage FROM batch_stage WHERE batch_id=? AND status NOT IN ('done', 'skipped') ORDER BY ord", (batch,))]
        if open_stages and b["status"] != "failed":
            raise ReinsError(f"cannot close: stages not done or skipped: {', '.join(open_stages)}")
        con.execute("UPDATE batch SET status='closed', closed=? WHERE batch_id=?", (now(), batch))
        _event(con, batch, "closed")
    leases.release(con, f"batch:{batch}", force=True)


def spent(con, batch: str, stage: str | None = None) -> float:
    q, args = "SELECT COALESCE(SUM(amount),0) FROM spend WHERE batch_id=?", [batch]
    r, rargs = "SELECT COALESCE(SUM(amount),0) FROM spend_reserve WHERE batch_id=? AND state='open'", [batch]
    if stage:
        q += " AND stage=?"; args.append(stage); r += " AND stage=?"; rargs.append(stage)
    return con.execute(q, args).fetchone()[0] + con.execute(r, rargs).fetchone()[0]


def status(con, batch: str) -> dict:
    b = dict(get(con, batch))
    b["aliases"] = json.loads(b["aliases"])
    b["spent"] = round(spent(con, batch), 4)
    stages = []
    for st in con.execute("SELECT * FROM batch_stage WHERE batch_id=? ORDER BY ord", (batch,)):
        led = ledger(con, batch, st["stage"])
        stages.append({**dict(st), **{k: led[k] for k in ("done", "skipped", "failed", "running")},
                       "missing": len(led["missing"]), "spent": round(spent(con, batch, st["stage"]), 4)})
    events = [dict(r) for r in con.execute(
        "SELECT at, event, detail FROM batch_event WHERE batch_id=? ORDER BY id DESC LIMIT 5", (batch,))]
    procs = [dict(r) for r in con.execute("SELECT * FROM process WHERE batch_id=? AND state IN ('running','paused')",
                                          (batch,))]
    return {"batch": b, "stages": stages, "recent_events": events, "processes": procs}


def case_history(con, oachargeid: str) -> list[dict]:
    """C1 record continuity: everything that happened to one case, across batches and stages, in order."""
    return [dict(r) for r in con.execute(
        "SELECT e.at, e.batch_id, b.type, e.stage, e.status, e.reason, e.module_version"
        " FROM case_event e JOIN batch b USING (batch_id) WHERE e.oachargeid=? ORDER BY e.id", (oachargeid,))]


def live(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM batch WHERE status NOT IN ('closed','failed','done') ORDER BY created DESC")]
