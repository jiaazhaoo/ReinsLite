"""reins.db write lock (2026-11-09): ~4 paid calls a second each held the lock ~0.35 s for their cap checks (whole-table
sums of spend / spend_reserve under BEGIN IMMEDIATE), so it was busy ~70% of the time and merges failed. The checks read
covering indexes and only the windows a pool has a cap for; WAL commits skip the fsync; the watchdog warns when the
lock stays busy."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from reins import batches, spend, store, watchdog
from reins.store import connect, now, tx


class Base(unittest.TestCase):
    CONFIG = "[pool]\nhourly_cap = 1000.0\ndaily_cap = 500.0\nweekly_cap = 2000.0\nper_session_daily_cap = 1000.0\n\n[providers.gemini]\ndaily_cap = 5.0\n"

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-dblock-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "config.toml").write_text(self.CONFIG)
        self.con = connect(self.tmp)
        (self.tmp / "cases.txt").write_text("101\n")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Indexes(Base):
    def test_hot_indexes_and_wal_normal(self):
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for sql in store.HOT_INDEXES:
            self.assertIn(sql.split(" ON ")[0].split()[-1], names)
        self.assertEqual(self.con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.con.execute("PRAGMA synchronous").fetchone()[0], 1)       # NORMAL

    def test_the_cap_sums_read_an_index_only(self):
        plans = {q: " ".join(r[3] for r in self.con.execute("EXPLAIN QUERY PLAN " + q, a)) for q, a in (
            ("SELECT COALESCE(SUM(amount),0) FROM spend WHERE provider=? AND at>=?", ("gemini", "2026")),
            ("SELECT COALESCE(SUM(amount),0) FROM spend s JOIN batch b USING (batch_id) WHERE s.at>=?", ("2026",)),
            ("SELECT COALESCE(SUM(amount),0) FROM spend_reserve WHERE state='open' AND provider=? AND at>=?", ("gemini", "2026")),
            ("SELECT COALESCE(SUM(amount),0) FROM spend_reserve WHERE batch_id=? AND state='open'", ("b",)))}
        for q, plan in plans.items():
            self.assertIn("COVERING INDEX", plan, q)


class PoolChecks(Base):
    def running(self, cap=100.0):
        b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="caps",
                          case_file=self.tmp / "cases.txt", stages=[batches.parse_stage_spec("s:paid")], spend_cap=cap)
        batches.stage_start(self.con, b, "s")
        return b

    def test_windows_not_asked_are_left_out(self):
        u = spend.pool_usage(self.con, None, open_caps=False, windows=["today"], sessions=False)
        self.assertEqual((u["hour"], u["week"], u["open_caps"], u["today_by_session"]), (None, None, None, {}))
        self.assertEqual(u["today"], 0)
        full = spend.pool_usage(self.con, None)
        self.assertIsNotNone(full["week"])
        self.assertIsNotNone(full["open_caps"])

    def test_the_caps_still_hold(self):
        b = self.running(cap=1000.0)
        with tx(self.con):
            self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?,?,?,?,?,?)",
                             (now(), b, "s", "openrouter", "m", 499.0, "table"))
        spend.pool_check(self.con, b, 0.5)                                   # 499.5 <= daily 500
        with self.assertRaises(spend.CapReached):
            spend.pool_check(self.con, b, 2.0)                               # 501 > daily 500
        with tx(self.con):
            self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?,?,?,?,?,?)",
                             (now(), b, "s", "gemini", "m", 4.9, "table"))
        with self.assertRaises(spend.ProviderCapReached):
            spend.provider_check(self.con, "gemini", 0.2)                    # 5.1 > gemini daily 5


class LockWatch(Base):
    def setUp(self):
        super().setUp()
        watchdog.LOCK_SAMPLES.clear()

    def test_share_counts_a_held_lock(self):
        db = self.tmp / "reins.db"
        self.assertEqual(watchdog.lock_busy_share(db, tries=5, gap=0), 0.0)
        holder = sqlite3.connect(db, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            self.assertEqual(watchdog.lock_busy_share(db, tries=5, gap=0), 1.0)
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_warns_only_after_rounds_above_the_line(self):
        cfg = {"db_lock_warn_pct": 30}
        for _ in range(watchdog.LOCK_ROUNDS - 1):
            self.assertEqual(watchdog.check_db_lock(self.con, cfg, sample=lambda: 0.7), [])
        self.assertEqual(watchdog.check_db_lock(self.con, cfg, sample=lambda: 0.7), ["db write lock busy 70%"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM notification WHERE key='db_lock'").fetchone()[0], 1)
        watchdog.LOCK_SAMPLES.clear()
        for _ in range(watchdog.LOCK_ROUNDS):
            out = watchdog.check_db_lock(self.con, cfg, sample=lambda: 0.1)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
