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
