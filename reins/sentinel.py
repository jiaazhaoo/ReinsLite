"""C8b quality sentinel: a run that goes wrong while every process "succeeds".

The watchdog sees processes, time and resources; the gate sees code before it ships. Neither sees a stage that exits 0
and marks every case done or skipped for the wrong reason: a paid stage whose calls never reached the gateway (a client
library ignoring <ENV>_BASE_URL), judges that "answered nothing" because the gateway refused them, a stage whose input
file was empty. These stage ends looked normal on the board until a person opened the results.

At every stage end the sentinel compares the stage with itself and with the same project's earlier batches:

    paid_no_calls   a paid stage finished cases without a single gateway call (hits from the cache count as calls)
    one_reason      at least `reason_share` of the cases ended not-done with one and the same reason, and the same
                    project's earlier batches of this stage did not (a usual reason is not news)
    rate_jump       the share of not-done cases is far above what this stage usually has
                    (> max(rate_jump x median, median + rate_margin) over the last earlier batches)

On a production or rework batch a finding fails the stage (the next stage cannot start), pauses the batch (the runner
does not retry into the same wall), opens an issue on the affected cases and tells the batch owner. On other batch
types it only notifies. Thresholds: $REINS_HOME/config.toml [sentinel]. Adopted outcomes are ignored.

While a paid stage runs, the watchdog also asks: has it been running `paid_idle_min` minutes without one gateway call?

A finding that is in fact normal for the workflow (every case of a rework skips `prepare`, a batch of cases without
scans) is acknowledged once by a person:  reins sentinel ack BATCH STAGE --why '...'
That stage counts as done again, and the same finding (project, check, reason) is not raised again on later stages of
the batch or on this stage of later batches.
"""
from __future__ import annotations

import statistics
from collections import Counter

from .store import ReinsError, config, now, tx

DEFAULTS = {"min_cases": 20, "reason_share": 0.9, "rate_jump": 2.0, "rate_margin": 0.25, "history": 10,
            "paid_idle_min": 30}
STRICT = ("production", "rework")


SCHEMA = """
CREATE TABLE IF NOT EXISTS sentinel_ack (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  project   TEXT NOT NULL,
  batch_id  TEXT NOT NULL,
  stage     TEXT NOT NULL,
  check_name TEXT NOT NULL,
  reason    TEXT NOT NULL,
  why       TEXT NOT NULL,
  session   TEXT
)"""


def ensure(con) -> None:
    con.execute(SCHEMA)


def _acked(con, batch: str, stage: str, check: str, reason: str) -> bool:
    ensure(con)
    proj = con.execute("SELECT project FROM batch WHERE batch_id=?", (batch,)).fetchone()["project"]
    return bool(con.execute("SELECT 1 FROM sentinel_ack WHERE project=? AND check_name=? AND reason=? AND (batch_id=? OR stage=?)",
                            (proj, check, reason, batch, stage)).fetchone())


def _cfg() -> dict:
    return {**DEFAULTS, **(config().get("sentinel") or {})}


def _profile(con, batch: str, stage: str) -> dict | None:
    rows = con.execute("SELECT case_id, status, reason FROM case_current WHERE batch_id=? AND stage=?", (batch, stage)).fetchall()
    rows = [r for r in rows if not (r["reason"] or "").startswith("adopted")]
    if not rows:
        return None
    bad = [r for r in rows if r["status"] in ("skipped", "failed")]
    reasons = Counter((r["reason"] or "").strip() for r in bad if (r["reason"] or "").strip())
    top, k = reasons.most_common(1)[0] if reasons else ("", 0)
    return {"n": len(rows), "bad": len(bad), "rate": len(bad) / len(rows), "top": top, "top_n": k,
            "top_cases": [r["case_id"] for r in bad if (r["reason"] or "").strip() == top],
            "bad_cases": [r["case_id"] for r in bad], "done": sum(1 for r in rows if r["status"] == "done")}


def _history(con, batch: str, stage: str, cfg: dict) -> list[dict]:
    b = con.execute("SELECT project, created FROM batch WHERE batch_id=?", (batch,)).fetchone()
    out = []
    for r in con.execute("SELECT s.batch_id FROM batch_stage s JOIN batch x USING (batch_id) WHERE x.project=? AND s.stage=?"
                         " AND s.batch_id<>? AND x.created<? AND s.status IN ('done','failed') ORDER BY x.created DESC LIMIT ?",
                         (b["project"], stage, batch, b["created"], cfg["history"])):
        p = _profile(con, r["batch_id"], stage)
        if p and p["n"] >= cfg["min_cases"]:
            out.append(p)
    return out


def findings(con, batch: str, stage: str) -> list[dict]:
    """What is wrong with this stage's outcomes: [{check, detail, cases}], minus what a person acknowledged."""
    p = _profile(con, batch, stage)
    return [x for x in _raw_findings(con, batch, stage) if not _acked(con, batch, stage, x["check"], p["top"] if p else "")]


def _raw_findings(con, batch: str, stage: str) -> list[dict]:
    cfg = _cfg()
    p = _profile(con, batch, stage)
    if not p or p["n"] < cfg["min_cases"]:
        return []
    st = con.execute("SELECT paid FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()
    out = []
    if st and st["paid"] and p["done"] > 0:
        calls = con.execute("SELECT COUNT(*) FROM spend WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()[0]
        if calls == 0:
            out.append({"check": "sentinel_paid_no_calls", "cases": [],
                        "detail": f"paid stage finished {p['done']} cases without one call through the gateway: the "
                                  f"pipeline is not using <ENV>_BASE_URL, or its calls fail before they are sent"})
    hist = _history(con, batch, stage, cfg)
    if p["top_n"] >= cfg["reason_share"] * p["n"]:
        usual = sum(1 for h in hist if h["top"] == p["top"] and h["top_n"] >= 0.5 * p["top_n"] * h["n"] / p["n"])
        if usual < 2:
            out.append({"check": "sentinel_one_reason", "cases": p["top_cases"],
                        "detail": f"{p['top_n']} of {p['n']} cases ended {p['top']!r}"})
    rates = [h["rate"] for h in hist]
    if len(rates) >= 2:
        med = statistics.median(rates)
        if p["rate"] > max(cfg["rate_jump"] * med, med + cfg["rate_margin"]):
            out.append({"check": "sentinel_rate_jump", "cases": p["bad_cases"],
                        "detail": f"{p['rate']:.0%} of cases not done; this stage usually has {med:.0%} "
                                  f"({len(rates)} earlier batches)"})
    return out


def stage_checks(con, batch: str, stage: str) -> tuple[list[tuple[str, bool, str]], list[dict]]:
    """(checks for stage_end, findings). Checks fail the stage only on strict batch types."""
    f = findings(con, batch, stage)
    b = con.execute("SELECT type FROM batch WHERE batch_id=?", (batch,)).fetchone()
    if not f or b["type"] not in STRICT:
        return [], f
    return [(x["check"], False, x["detail"]) for x in f], f


def act(con, batch: str, stage: str, found: list[dict]) -> None:
    """After a stage end with findings: on strict batches pause, open an issue, tell the owner; else notify."""
    if not found:
        return
    from . import batches, inbox, issues, notify
    b = batches.get(con, batch)
    text = "; ".join(f"{x['check'].removeprefix('sentinel_')}: {x['detail']}" for x in found)
    if b["type"] not in STRICT:
        notify.send(con, f"sentinel:{batch}:{stage}", "warn", f"{batch}/{stage}: the results look wrong", text, cooldown_min=0)
        return
    try:
        batches.set_status(con, batch, "paused", f"sentinel: {text}"[:500], system=True)
    except Exception:                                                       # noqa: BLE001
        pass
    cases = next((x["cases"] for x in found if x["cases"]), None)
    if not cases:
        cases = [r[0] for r in con.execute("SELECT case_id FROM case_current WHERE batch_id=? AND stage=? LIMIT 50",
                                           (batch, stage))]
    iid = None
    try:
        iid = issues.open_(con, batch, sorted(set(cases))[:200], f"sentinel at the end of {stage}: {text}"[:600], stage=stage)
    except Exception:                                                       # noqa: BLE001
        pass
    with tx(con):
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?, 'sentinel', ?)",
                    (now(), batch, f"{stage}: {text}"[:1000]))
    notify.send(con, f"sentinel:{batch}:{stage}", "action", f"{batch} paused: {stage} results look wrong",
                f"{text}\n{'issue #' + str(iid) if iid else ''} -- fix the cause, then start {stage} again "
                f"(reins batch stage-start {batch} {stage}) and reins ctl resume {batch}", cooldown_min=0)
    inbox.to_batch_owner(con, batch, f"{batch} paused by the sentinel at the end of {stage}",
                         text + (f" (issue #{iid})" if iid else ""))


def ack(con, batch: str, stage: str, why: str) -> dict:
    """A person says the findings of this stage are normal: record it, close the stage done if only the sentinel
    failed it, and leave the batch to be resumed. Returns what was acknowledged."""
    from . import batches
    from .store import session
    if len(why.strip()) < 6:
        raise ReinsError("ack needs --why: why these results are normal for this workflow")
    ensure(con)
    b = batches.get(con, batch)
    found = [x for x in _raw_findings(con, batch, stage)]
    if not found:
        raise ReinsError(f"{batch}/{stage}: the sentinel finds nothing to acknowledge")
    p = _profile(con, batch, stage)
    with tx(con):
        for x in found:
            con.execute("INSERT INTO sentinel_ack (at, project, batch_id, stage, check_name, reason, why, session)"
                        " VALUES (?,?,?,?,?,?,?,?)", (now(), b["project"], batch, stage, x["check"], p["top"], why.strip(), session()))
        st = con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()
        last = con.execute("SELECT MAX(at) FROM stage_check WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()[0]
        others = con.execute("SELECT COUNT(*) FROM stage_check WHERE batch_id=? AND stage=? AND at=? AND ok=0"
                             " AND check_name NOT LIKE 'sentinel_%'", (batch, stage, last)).fetchone()[0] if last else 0
        reopened = False
        if st and st["status"] == "failed" and not others:
            from . import state
            state.stage_to(con, batch, stage, "done", f"sentinel findings acknowledged: {why.strip()}"[:1000])
            reopened = True
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?, 'sentinel_ack', ?)",
                    (now(), batch, f"{stage}: {', '.join(x['check'] for x in found)}; {why.strip()}"[:1000]))
    return {"acknowledged": [x["check"] for x in found], "stage_done": reopened}


def watch_running(con) -> list[str]:
    """Watchdog pass: paid stages running a while with no gateway call yet. Notifies once per stage."""
    from . import notify
    cfg = _cfg()
    out = []
    for r in con.execute("SELECT s.batch_id, s.stage, s.started FROM batch_stage s JOIN batch b USING (batch_id)"
                         " WHERE s.paid=1 AND s.status='running' AND b.status='running' AND s.started IS NOT NULL"):
        import datetime as dt
        mins = (dt.datetime.now() - dt.datetime.fromisoformat(r["started"])).total_seconds() / 60
        if mins < cfg["paid_idle_min"]:
            continue
        calls = con.execute("SELECT COUNT(*) FROM spend WHERE batch_id=? AND stage=?", (r["batch_id"], r["stage"])).fetchone()[0]
        if calls == 0:
            notify.send(con, f"paid_idle:{r['batch_id']}:{r['stage']}", "warn",
                        f"{r['batch_id']}/{r['stage']}: paid stage running {mins:.0f} min, no gateway call yet",
                        "if this stage should be calling models by now, its client may be bypassing the gateway "
                        "(<ENV>_BASE_URL ignored) and its calls fail or go unrecorded", cooldown_min=24 * 60)
            out.append(f"{r['batch_id']}/{r['stage']}: paid, no calls after {mins:.0f} min")
    return out
