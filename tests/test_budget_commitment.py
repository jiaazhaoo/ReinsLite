"""Approving a batch cap checks what today's pool is really committed to: spent or reserved today plus what live
batches may still spend. Old caps already spent, and batches paused for a day or more, hold nothing.

Regression for 2026-10-21: a $1.50 experiment was refused on a day with $0 spent because the full caps of 21 unclosed
batches ($264.85, mostly spent days before) were added up against the daily cap; over ten days the daily cap had been
raised 20 -> 260 to get past the same check while no day spent more than $73."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, spend
from reins.store import ReinsError, connect, tx


class Commitment(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-bc-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        (self.tmp / "home" / "config.toml").write_text("[pool]\ndaily_cap = 20.0\nmax_batch_cap = 300.0\n")
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, status, cap, spent, idle_h):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="budget",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 1,
                                                                     "spend_cap": None, "time_limit_s": None}])
        at = (dt.datetime.now() - dt.timedelta(hours=idle_h)).isoformat(timespec="seconds")
        with tx(self.con):
            self.con.execute("UPDATE batch SET status=?, spend_cap=?, created=? WHERE batch_id=?", (status, cap, at, b))
            self.con.execute("UPDATE batch_event SET at=? WHERE batch_id=?", (at, b))
            if spent:
                self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?, 's', 'x', 'm', ?, 'provider')",
                                 (at, b, spent))
        return b

    def test_spent_and_dormant_caps_hold_nothing(self):
        self.batch("paused", 200.0, 199.0, idle_h=48)        # old, spent, paused for two days: holds nothing
        self.batch("paused", 30.0, 2.0, idle_h=72)           # dormant: holds nothing until it runs again
        live = self.batch("running", 15.0, 10.0, idle_h=1)    # may still spend 5
        c = spend.commitment(self.con)
        self.assertEqual(c["held_total"], 5.0)
        new = self.batch("open", 0.0, 0.0, idle_h=0)
        batches.approve_spend(self.con, new, 1.5)           # 10 spent today + 5 + 1.5 <= 20
        with self.assertRaises(ReinsError) as e:
            batches.approve_spend(self.con, new, 18.0)        # 10 + 5 + 18 > 20
        msg = str(e.exception)
        self.assertIn(f"reins batch approve {live} --cap 10.00", msg)   # the way out is shrinking, named
        self.assertIn("not the way past this check", msg)
        batches.approve_spend(self.con, live, 10.0)          # shrink to what it spent
        batches.approve_spend(self.con, new, 9.0)            # $10 spent today (by the live batch) + 9 <= 20


if __name__ == "__main__":
    unittest.main()
