"""The board groups live batches by what they need: moving / needs you / stuck / long idle, each with a command."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, board
from reins.store import ReinsError, connect, now, tx


class Triage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-tr-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.n = 0

    def batch(self, type_, status, reason="", idle_h=0.0):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose=f"triage {self.n}",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 0,
                                                                     "spend_cap": None, "time_limit_s": None}])
        import datetime as dt
        at = (dt.datetime.now() - dt.timedelta(hours=idle_h)).isoformat(timespec="seconds")
        with tx(self.con):
            self.con.execute("UPDATE batch SET type=?, status=?, status_reason=? WHERE batch_id=?", (type_, status, reason, b))
            self.con.execute("UPDATE batch_event SET at=? WHERE batch_id=?", (at, b))
        return b

    def test_groups_and_commands(self):
        moving = self.batch("experiment", "running", idle_h=0.2)
        disk = self.batch("production", "paused", "disk /data at 90%", idle_h=50)
        old = self.batch("experiment", "paused", "harness bug", idle_h=80)
        stuck = self.batch("rework", "running", idle_h=5)
        forgotten = self.batch("eval", "running", idle_h=100)
        g = {c["batch_id"]: c for c in board.running_cards(self.con)}
        self.assertEqual(g[moving]["group"], "active")
        self.assertEqual((g[disk]["group"], g[disk]["kind"]), ("decide", "disk"))       # production stays a decision
        self.assertIn("reins ctl resume", g[disk]["cmd"])
        self.assertEqual(g[old]["group"], "stale")                                      # an old paused experiment
        self.assertEqual(g[stuck]["group"], "stuck")
        self.assertEqual(g[forgotten]["group"], "stale")
        self.assertIn("abandon", g[forgotten]["cmd"])

    def test_abandon_stale_and_refuse_fresh(self):
        old = self.batch("experiment", "running", idle_h=80)
        fresh = self.batch("experiment", "running", idle_h=1)
        os.environ["REINS_SESSION"] = "someone-else"
        with self.assertRaises(ReinsError):
            batches.abandon(self.con, fresh, "not needed any more")                     # not idle long enough
        batches.abandon(self.con, old, "superseded by a later run")
        self.assertEqual(batches.get(self.con, old)["status"], "closed")
        self.assertEqual([s["batch_id"] for s in batches.stale(self.con, 24)], [])


if __name__ == "__main__":
    unittest.main()
