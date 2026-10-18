"""C8b: a stage that 'succeeds' with wrong results is stopped; a person can mark a finding normal once; stale holds go."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, inbox, sentinel, watchdog
from reins.store import config, connect, now, tx

CASES = [str(100 + i) for i in range(25)]


class Sentinel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-sn-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("\n".join(CASES) + "\n")

    def batch(self, type_="rework", paid=0):
        st = [{"stage": s, "module_version": None, "paid": paid if s == "judge" else 0, "spend_cap": None, "time_limit_s": None}
              for s in ("prepare", "judge")]
        self.n = getattr(self, "n", 0) + 1                                  # the id keeps "experiment": a scope each
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="sentinel test",
                          case_file=self.tmp / "cases.txt", stages=st, spend_cap=1.0 if paid else 0.0)
        with tx(self.con):
            self.con.execute("UPDATE batch SET type=?, preflight_ok=1 WHERE batch_id=?", (type_, b))   # strict types need a release otherwise
        return b

    def test_one_reason_fails_pauses_opens_issue_then_ack(self):
        b = self.batch()
        batches.stage_start(self.con, b, "prepare")
        batches.mark(self.con, b, "prepare", [(c, "skipped", "input file was empty") for c in CASES])
        led = batches.stage_end(self.con, b, "prepare")
        self.assertEqual(led["status"], "failed")
        self.assertEqual([f["check"] for f in led["sentinel"]], ["sentinel_one_reason"])
        self.assertEqual(batches.get(self.con, b)["status"], "paused")
        self.assertTrue(self.con.execute("SELECT 1 FROM issue WHERE batch_id=? AND symptom LIKE 'sentinel%'", (b,)).fetchone())
        r = sentinel.ack(self.con, b, "prepare", "every case of this kind of batch skips prepare")
        self.assertTrue(r["stage_done"])
        st = self.con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage='prepare'", (b,)).fetchone()[0]
        self.assertEqual(st, "done")
        b2 = self.batch()                                                    # the same finding on a later batch: normal now
        batches.stage_start(self.con, b2, "prepare")
        batches.mark(self.con, b2, "prepare", [(c, "skipped", "input file was empty") for c in CASES])
        self.assertEqual(batches.stage_end(self.con, b2, "prepare")["status"], "done")

    def test_paid_stage_without_gateway_calls(self):
        b = self.batch(paid=1)
        batches.stage_start(self.con, b, "prepare")
        batches.mark(self.con, b, "prepare", [(c, "done", None) for c in CASES])
        batches.stage_end(self.con, b, "prepare")
        batches.stage_start(self.con, b, "judge")
        batches.mark(self.con, b, "judge", [(c, "done", None) for c in CASES])
        led = batches.stage_end(self.con, b, "judge")
        self.assertIn("sentinel_paid_no_calls", [f["check"] for f in led["sentinel"]])

    def test_experiments_only_notify(self):
        b = self.batch(type_="experiment")
        batches.stage_start(self.con, b, "prepare")
        batches.mark(self.con, b, "prepare", [(c, "skipped", "input file was empty") for c in CASES])
        self.assertEqual(batches.stage_end(self.con, b, "prepare")["status"], "done")
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key LIKE 'sentinel:%'").fetchone())

    def test_stale_holds_are_released(self):
        b = self.batch()
        with tx(self.con):
            self.con.execute("INSERT INTO spend_reserve (at, batch_id, stage, amount, state) VALUES "
                             "('2026-01-01T00:00:00', ?, 'judge', 0.5, 'open')", (b,))
            self.con.execute("INSERT INTO spend_reserve (at, batch_id, stage, amount, state) VALUES (?, ?, 'judge', 0.2, 'open')", (now(), b))
        out = watchdog.release_stale_reservations(self.con, config())
        self.assertEqual(out, ["released 1 stale spend hold(s)"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend_reserve WHERE state='open'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
