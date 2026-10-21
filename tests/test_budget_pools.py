"""Two budget pools: production (production and rework batches) and development (everything else), each with its own
limits under the machine's. A development experiment cannot eat production's day, and the reverse."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, settings_edit, spend
from reins.spend import CapReached
from reins.store import ReinsError, connect, now, tx


class Pools(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-pools-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        (self.tmp / "home" / "config.toml").write_text(
            "[pool]\ndaily_cap = 100.0\nweekly_cap = 300.0\nhourly_cap = 50.0\nmax_batch_cap = 80.0\nper_session_daily_cap = 80.0\n")
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, type_, spent=0.0):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="pools",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 1,
                                                                     "spend_cap": None, "time_limit_s": None}])
        with tx(self.con):
            self.con.execute("UPDATE batch SET type=? WHERE batch_id=?", (type_, b))
            if spent:
                self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?, 's', 'x', 'm', ?, 'provider')",
                                 (now(), b, spent))
        return b

    def test_types_go_to_their_pool_and_limits_are_separate(self):
        self.assertEqual((spend.pool_of("rework"), spend.pool_of("experiment"), spend.pool_of("training")),
                         ("production", "development", "development"))
        settings_edit.set_(self.con, "pool.development.daily_cap", "5", "experiments get five dollars a day")
        settings_edit.set_(self.con, "pool.production.daily_cap", "60", "production runs")
        self.batch("experiment", spent=4.0)
        prod = self.batch("production", spent=30.0)
        exp = self.batch("experiment")
        with self.assertRaises(ReinsError) as e:
            batches.approve_spend(self.con, exp, 2.0)                    # 4 today + 2 > 5 in development
        self.assertIn("development pool", str(e.exception))
        batches.approve_spend(self.con, exp, 1.0)
        batches.approve_spend(self.con, prod, 50.0)                      # production: 30 spent of its own 60, development untouched
        with self.assertRaises(CapReached) as e:
            spend.pool_check(self.con, exp, 1.5)                         # per call: 4 + 1.5 > 5
        self.assertIn("development pool", str(e.exception))
        spend.pool_check(self.con, prod, 1.5)
        v = {p["pool"]: p for p in spend.pools_view(self.con)}
        self.assertEqual((v["development"]["today"], v["production"]["today"], v["machine"]["today"]), (4.0, 30.0, 34.0))
        self.assertEqual(v["development"]["limits"]["daily_cap"], 5)

    def test_without_pool_tables_only_the_machine_limits(self):
        self.batch("experiment", spent=90.0)
        exp = self.batch("experiment")
        with self.assertRaises(ReinsError) as e:
            batches.approve_spend(self.con, exp, 20.0)                  # 90 + 20 > the machine's 100
        self.assertIn("machine", str(e.exception))


if __name__ == "__main__":
    unittest.main()
