"""C9 spend ledger: reserve before a paid call, settle after it. Caps are checked against reserved + settled, so
forty concurrent calls cannot overshoot the way counting-after-the-fact did ($7 cap -> $7.47)."""
from __future__ import annotations

from . import batches
from .store import ReinsError, config, now, tx

DEFAULT_PRICE = (2.0, 12.0)          # USD per million tokens when the model is not in the table; flagged 'default'


class CapReached(ReinsError):
    pass


def price(con, model: str) -> tuple[tuple[float, float], str]:
    row = con.execute("SELECT input_per_m, output_per_m FROM price WHERE model=? ORDER BY since DESC LIMIT 1",
                      (model,)).fetchone()
    return ((row[0], row[1]), "table") if row else (DEFAULT_PRICE, "default")


def set_price(con, model: str, input_per_m: float, output_per_m: float, source: str = "") -> None:
    with tx(con):
        con.execute("INSERT OR REPLACE INTO price VALUES (?,?,?,?,?)", (model, input_per_m, output_per_m, now(), source))


def estimate(con, batch: str, stage: str, model: str) -> float:
    """Expected cost of one uncached call: this batch+stage's average, else this model's global average, else config."""
    for q, args in (("SELECT AVG(amount) FROM spend WHERE batch_id=? AND stage=? AND model=? AND cache_hit=0 AND amount>0",
                     (batch, stage, model)),
                    ("SELECT AVG(amount) FROM (SELECT amount FROM spend WHERE model=? AND cache_hit=0 AND amount>0"
                     " ORDER BY id DESC LIMIT 200)", (model,))):
        v = con.execute(q, args).fetchone()[0]
        if v:
            return float(v)
    return float(config()["default_call_estimate"])


# ------------------------------------------------------------------ the pool: all sessions together
def pool_usage(con) -> dict:
    """Spent + reserved across every batch: last hour, today, this week; today per owning session."""
    import datetime as _dt
    now_ = _dt.datetime.now()
    hour = (now_ - _dt.timedelta(hours=1)).isoformat(timespec="seconds")
    today = now_.date().isoformat()
    week = (now_.date() - _dt.timedelta(days=7)).isoformat()

    def spent_since(ts, extra="", args=()):
        s = con.execute(f"SELECT COALESCE(SUM(amount),0) FROM spend s JOIN batch b USING (batch_id) WHERE s.at>=? {extra}", (ts, *args)).fetchone()[0]
        r = con.execute(f"SELECT COALESCE(SUM(amount),0) FROM spend_reserve s JOIN batch b USING (batch_id) WHERE state='open' AND s.at>=? {extra}", (ts, *args)).fetchone()[0]
        return s + r

    by_session = {r[0]: r[1] for r in con.execute(
        "SELECT b.owner_session, COALESCE(SUM(s.amount),0) FROM spend s JOIN batch b USING (batch_id) WHERE s.at>=?"
        " GROUP BY b.owner_session", (today,))}
    return {"hour": spent_since(hour), "today": spent_since(today), "week": spent_since(week), "today_by_session": by_session,
            "open_caps": con.execute("SELECT COALESCE(SUM(spend_cap),0) FROM batch WHERE status IN ('open','running','paused')").fetchone()[0]}


def pool_check(con, batch: str, amount: float) -> None:
    """Would this spend break the pool? Raises CapReached naming the limit."""
    pool = config()["pool"]
    u = pool_usage(con)
    b = batches.get(con, batch)
    if u["hour"] + amount > pool["hourly_cap"]:
        raise CapReached(f"pool: ${u['hour']:.2f} spent in the last hour + ${amount:.3f} > hourly cap ${pool['hourly_cap']:.2f}")
    if u["today"] + amount > pool["daily_cap"]:
        raise CapReached(f"pool: ${u['today']:.2f} spent today + ${amount:.3f} > daily cap ${pool['daily_cap']:.2f}")
    if u["week"] + amount > pool["weekly_cap"]:
        raise CapReached(f"pool: ${u['week']:.2f} spent this week + ${amount:.3f} > weekly cap ${pool['weekly_cap']:.2f}")
    sess = b["owner_session"]
    if sess and u["today_by_session"].get(sess, 0) + amount > pool["per_session_daily_cap"]:
        raise CapReached(f"pool: session {sess[:8]} spent ${u['today_by_session'][sess]:.2f} today + ${amount:.3f} > "
                         f"per-session daily cap ${pool['per_session_daily_cap']:.2f}")


def caps(con, batch: str, stage: str) -> tuple[float, float | None]:
    b = batches.get(con, batch)
    st = con.execute("SELECT spend_cap FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()
    if not st:
        raise ReinsError(f"stage {stage!r} is not planned in {batch}")
    return b["spend_cap"], st["spend_cap"]


def reserve(con, batch: str, stage: str, amount: float) -> int:
    """Hold `amount` against the caps. Raises CapReached (nothing reserved) when it would not fit."""
    with tx(con):
        b = batches.get(con, batch)
        if b["status"] != "running":
            raise ReinsError(f"batch {batch} is {b['status']}; paid calls run only while it is running")
        bcap, scap = caps(con, batch, stage)
        if bcap <= 0:
            raise CapReached(f"{batch}: spend cap is $0 -- paid stages need `reins batch approve {batch} --cap USD`")
        used_b = batches.spent(con, batch)
        if used_b + amount > bcap:
            raise CapReached(f"{batch}: ${used_b:.3f} used/reserved + ${amount:.3f} > batch cap ${bcap:.2f}")
        if scap is not None:
            used_s = batches.spent(con, batch, stage)
            if used_s + amount > scap:
                raise CapReached(f"{batch}/{stage}: ${used_s:.3f} + ${amount:.3f} > stage cap ${scap:.2f}")
        pool_check(con, batch, amount)
        cur = con.execute("INSERT INTO spend_reserve (at, batch_id, stage, amount, state) VALUES (?,?,?,?, 'open')",
                          (now(), batch, stage, amount))
        return cur.lastrowid


def settle(con, reserve_id: int, *, provider: str, model: str, amount: float, priced: str, tokens_in: int | None,
           tokens_out: int | None, cache_hit: bool, request_sha: str, http_status: int | None,
           module_version: str | None = None) -> None:
    with tx(con):
        r = con.execute("SELECT * FROM spend_reserve WHERE id=?", (reserve_id,)).fetchone()
        if not r or r["state"] != "open":
            raise ReinsError(f"reserve {reserve_id} is not open")
        con.execute("UPDATE spend_reserve SET state='settled' WHERE id=?", (reserve_id,))
        con.execute("INSERT INTO spend (at, batch_id, stage, module_version, provider, model, amount, priced, tokens_in,"
                    " tokens_out, cache_hit, request_sha, reserve_id, http_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now(), r["batch_id"], r["stage"], module_version, provider, model, amount, priced, tokens_in,
                     tokens_out, int(cache_hit), request_sha, reserve_id, http_status))


def release(con, reserve_id: int) -> None:
    """The call never happened (network error, refused): give the hold back."""
    with tx(con):
        con.execute("UPDATE spend_reserve SET state='released' WHERE id=? AND state='open'", (reserve_id,))


def record_free(con, batch: str, stage: str, *, provider: str, model: str, request_sha: str,
                module_version: str | None = None) -> None:
    """A cache hit: nothing charged, still a row (hit rate feeds the estimate)."""
    with tx(con):
        con.execute("INSERT INTO spend (at, batch_id, stage, module_version, provider, model, amount, priced, cache_hit,"
                    " request_sha) VALUES (?,?,?,?,?,?,0,'cache',1,?)",
                    (now(), batch, stage, module_version, provider, model, request_sha))


def summary(con, batch: str) -> dict:
    rows = con.execute("SELECT stage, model, COUNT(*) n, SUM(cache_hit) hits, SUM(amount) usd FROM spend WHERE batch_id=?"
                       " GROUP BY stage, model ORDER BY stage, model", (batch,)).fetchall()
    bcap = batches.get(con, batch)["spend_cap"]
    return {"batch": batch, "cap": bcap, "spent": round(batches.spent(con, batch), 4),
            "by_stage_model": [dict(r) for r in rows]}


def weekly(con, weeks: int = 8) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT strftime('%Y-W%W', at) week, b.project, SUM(amount) usd, COUNT(*) calls, SUM(cache_hit) hits"
        " FROM spend s JOIN batch b USING (batch_id) GROUP BY week, b.project ORDER BY week DESC LIMIT ?", (weeks * 5,))]


# ------------------------------------------------------------------ provider balances (free endpoints)
def _key(ledger: str) -> str | None:
    from . import providers
    spec = providers.by_ledger(ledger)
    k = providers.key(spec) if spec else None
    return k or None


def poll_balance(con, ledger: str) -> dict | None:
    """One sample of a provider's balance from the free account endpoint it declares. None when it has none or no key."""
    import json as _json
    import urllib.request
    from . import providers
    spec = providers.by_ledger(ledger)
    k = _key(ledger)
    if not spec or not spec.get("balance") or not k:
        return None
    try:
        req = urllib.request.Request(spec["balance"]["url"], headers={"Authorization": "Bearer " + k})
        bal, used, detail = providers.parse_balance(spec["balance"]["parse"], _json.load(urllib.request.urlopen(req, timeout=20)))
    except Exception as e:                                                      # noqa: BLE001
        with tx(con):
            con.execute("INSERT INTO balance_sample (at, provider, detail) VALUES (?,?,?)", (now(), ledger, f"error: {e!s}"[:200]))
        return None
    with tx(con):
        con.execute("INSERT INTO balance_sample (at, provider, balance, usage_total, detail) VALUES (?,?,?,?,?)",
                    (now(), ledger, bal, used, detail))
    return {"provider": ledger, "balance": bal, "usage_total": used, "detail": detail}


def ledgers(con) -> list[str]:
    """Providers worth a card: in use by this installation, or present in the spend ledger."""
    from . import providers
    names = [p["ledger"] for p in providers.in_use()]
    names += [r[0] for r in con.execute("SELECT DISTINCT provider FROM spend") if r[0] and r[0] not in names]
    return names


def poll_balances(con) -> list[dict]:
    return [r for p in ledgers(con) if (r := poll_balance(con, p))]


def cost_view(con) -> list[dict]:
    """One card per provider: balance, spend today / this week (from balance samples where the provider reports usage,
    else from the gateway ledger), last call, calls today."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    week = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
    out = []
    from . import providers
    for p in ledgers(con):
        spec = providers.by_ledger(p) or {}
        latest = con.execute("SELECT * FROM balance_sample WHERE provider=? AND balance IS NOT NULL ORDER BY id DESC LIMIT 1", (p,)).fetchone()
        first_today = con.execute("SELECT * FROM balance_sample WHERE provider=? AND balance IS NOT NULL AND at>=? ORDER BY id LIMIT 1",
                                  (p, today)).fetchone()
        first_week = con.execute("SELECT * FROM balance_sample WHERE provider=? AND balance IS NOT NULL AND at>=? ORDER BY id LIMIT 1",
                                 (p, week)).fetchone()
        led_today = con.execute("SELECT COALESCE(SUM(amount),0), COUNT(*), MAX(at) FROM spend WHERE provider=? AND at>=?", (p, today)).fetchone()
        led_week = con.execute("SELECT COALESCE(SUM(amount),0) FROM spend WHERE provider=? AND at>=?", (p, week)).fetchone()[0]
        by_batch = [dict(r) for r in con.execute(
            "SELECT batch_id, ROUND(SUM(amount),2) usd FROM spend WHERE provider=? AND at>=? GROUP BY batch_id ORDER BY 2 DESC LIMIT 4", (p, week))]
        spent_today = spent_week = None
        src = "ledger"
        if latest and first_today:
            spent_today = round(first_today["balance"] - latest["balance"], 2); src = "balance"
        if latest and first_week:
            spent_week = round(first_week["balance"] - latest["balance"], 2)
        est = con.execute("SELECT COUNT(*) FROM spend WHERE provider=? AND priced='table' AND at>=?", (p, today)).fetchone()[0]
        out.append({"provider": p, "estimated_calls_today": est, "balance": latest["balance"] if latest else None,
                    "balance_at": latest["at"] if latest else None, "has_endpoint": bool(spec.get("balance")), "per_call": spec.get("kind") == "get",
                    "key_present": _key(p) is not None,
                    "spent_today": spent_today if spent_today is not None else round(led_today[0], 2),
                    "spent_week": spent_week if spent_week is not None else round(led_week, 2),
                    "spend_source": src, "calls_today": led_today[1], "last_call": led_today[2],
                    "by_batch": by_batch, "error": (latest is None and _key(p) is not None and bool(spec.get("balance")))})
    return out


# ------------------------------------------------------------------ all-time spend: ledger + imported history, reconciled
IMPORT_COLUMNS = ("day", "provider", "model", "purpose", "batch", "calls", "tokens_in", "tokens_out", "amount", "priced", "note")


def import_history(con, path, project: str, source: str) -> dict:
    """Spend that happened before (or outside) the gateway, from a CSV with IMPORT_COLUMNS (day, batch, calls,
    tokens and note may be empty). Append-only; the same file (by content) is refused the second time."""
    import csv as _csv
    from pathlib import Path as _P
    from . import names
    p = _P(path)
    sha = names.sha256_file(p)
    rows = list(_csv.DictReader(p.open(encoding="utf-8-sig")))
    missing = [c for c in ("provider", "model", "purpose", "amount", "priced") if c not in (rows[0].keys() if rows else [])]
    if not rows or missing:
        raise ReinsError(f"{p}: needs columns {', '.join(IMPORT_COLUMNS)} (missing {missing or 'rows'})")
    def num(v, f=float):
        return f(v) if v not in (None, "") else None
    with tx(con):
        if con.execute("SELECT 1 FROM spend_import_file WHERE import_id=?", (sha,)).fetchone():
            raise ReinsError(f"{p} was already imported (same content)")
        con.execute("INSERT INTO spend_import_file VALUES (?,?,?,?,?,?,?,?)",
                    (sha, now(), project, str(p.resolve()), source, len(rows), 0.0, None))
        total = 0.0
        for i, r in enumerate(rows, 1):
            if r["priced"] not in ("provider", "table", "estimate"):
                raise ReinsError(f"row {i}: priced must be provider | table | estimate")
            amt = float(r["amount"]); total += amt
            con.execute("INSERT INTO spend_import (import_id, project, day, provider, model, purpose, batch_label, calls,"
                        " tokens_in, tokens_out, amount, priced, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sha, project, r.get("day") or None, r["provider"], r["model"], r["purpose"], r.get("batch") or None,
                         num(r.get("calls"), int), num(r.get("tokens_in"), int), num(r.get("tokens_out"), int), amt,
                         r["priced"], r.get("note") or None))
        from .store import session as _session
        con.execute("UPDATE spend_import_file SET usd=?, session=? WHERE import_id=?", (round(total, 4), _session(), sha))
    return {"import_id": sha[:12], "rows": len(rows), "usd": round(total, 2)}


def all_time(con) -> dict:
    """Everything known to have been spent: the gateway ledger (by batch -> project) plus imported history, per
    provider reconciled with the provider's own cumulative usage where it reports one."""
    led = [dict(r) for r in con.execute(
        "SELECT b.project, substr(s.at,1,10) day, s.provider, s.model, s.stage purpose, s.batch_id batch,"
        " COUNT(*) calls, SUM(s.amount) usd, MIN(s.priced) priced"
        " FROM spend s LEFT JOIN batch b USING (batch_id) WHERE s.cache_hit=0"
        " GROUP BY 1,2,3,4,5,6")]
    imp = [dict(r) for r in con.execute(
        "SELECT project, day, provider, model, purpose, batch_label batch, SUM(COALESCE(calls,0)) calls, SUM(amount) usd,"
        " MIN(priced) priced FROM spend_import GROUP BY 1,2,3,4,5,6")]
    for r in led: r["source"] = "ledger"
    for r in imp: r["source"] = "import"
    rows = led + imp

    def group(keys):
        out = {}
        for r in rows:
            k = tuple(r[x] or "" for x in keys)
            g = out.setdefault(k, {**{x: r[x] for x in keys}, "usd": 0.0, "calls": 0, "ledger": 0.0, "import": 0.0})
            g["usd"] += r["usd"] or 0; g["calls"] += r["calls"] or 0; g[r["source"]] += r["usd"] or 0
        return sorted(out.values(), key=lambda g: -g["usd"])
    account = {}
    for r in con.execute("SELECT provider, usage_total, at FROM balance_sample WHERE usage_total IS NOT NULL"
                         " AND id IN (SELECT MAX(id) FROM balance_sample WHERE usage_total IS NOT NULL GROUP BY provider)"):
        account[r["provider"]] = {"usage_total": r["usage_total"], "at": r["at"]}
    by_provider = group(["provider"])
    for g in by_provider:
        a = account.get(g["provider"])
        g["account_usage"] = a["usage_total"] if a else None
        g["account_at"] = a["at"] if a else None
        g["unattributed"] = round(a["usage_total"] - g["usd"], 2) if a else None
    files = [dict(r) for r in con.execute("SELECT import_id, at, project, source, rows, usd FROM spend_import_file ORDER BY at")]
    total = sum(r["usd"] or 0 for r in rows)
    return {"total": round(total, 2), "ledger": round(sum(r["usd"] or 0 for r in led), 2),
            "imported": round(sum(r["usd"] or 0 for r in imp), 2),
            "unattributed": round(sum(g["unattributed"] for g in by_provider if g["unattributed"] and g["unattributed"] > 0), 2),
            "by_provider": by_provider, "by_project": group(["project"]),
            "by_purpose": group(["project", "purpose", "provider", "model"]), "by_batch": group(["project", "batch"]),
            "by_day": group(["day", "provider"]), "undated": round(sum(r["usd"] or 0 for r in rows if not r["day"]), 2),
            "imports": files}
