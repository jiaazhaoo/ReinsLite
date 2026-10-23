"""State invariants are checked and repaired by the system, never handed to the user (2026-10-23)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, invariants
from reins.store import connect, tx


def _stages(*names):
    return [{"stage": n, "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None} for n in names]


class Invariants(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-inv-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n103\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, type_, *stages):
        self.n += 1
        return batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_=type_, purpose="t",
                             case_file=self.tmp / "cases.txt", stages=_stages(*stages))

    def force_done(self, bid, stage_status="done"):          # what the other writers did: a status set without outcomes
        with tx(self.con):
            self.con.execute("UPDATE batch_stage SET status=? WHERE batch_id=?", (stage_status, bid))
            self.con.execute("UPDATE batch SET status='done' WHERE batch_id=?", (bid,))

    def test_strict_stage_done_without_outcomes_is_found_and_repaired(self):
        b = self.batch("intake", "s1")
        self.force_done(b)
        found = invariants.run(self.con)
        self.assertEqual([i["check"] for i in found], ["conservation"])
        r = invariants.tend(self.con)
        self.assertEqual(r["left"], 0)
        st = dict(self.con.execute("SELECT status, COUNT(*) FROM case_current WHERE batch_id=? GROUP BY status", (b,)).fetchall())
        self.assertEqual(st, {"skipped": 3})                    # no results file: honestly skipped, with the reason

    def test_experiment_without_any_outcome_is_not_per_case(self):
        b = self.batch("experiment", "s1")
        self.force_done(b)
        invariants.tend(self.con)
        self.assertEqual(self.con.execute("SELECT per_case FROM batch WHERE batch_id=?", (b,)).fetchone()[0], 0)
        self.assertEqual(invariants.run(self.con), [])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM case_event WHERE batch_id=?", (b,)).fetchone()[0], 0)

    def test_done_batch_with_unfinished_stage(self):
        b = self.batch("experiment", "s1", "s2")
        with tx(self.con):
            self.con.execute("UPDATE batch SET status='done', per_case=0 WHERE batch_id=?", (b,))
            self.con.execute("UPDATE batch_stage SET status='done' WHERE batch_id=? AND stage='s1'", (b,))
        self.assertEqual([i["check"] for i in invariants.run(self.con)], ["stage_order"])
        invariants.tend(self.con)
        self.assertEqual(self.con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage='s2'", (b,)).fetchone()[0], "skipped")

    def test_live_batch_is_not_repaired_but_escalated_to_a_developer(self):
        b = self.batch("intake", "s1")
        with tx(self.con):
            self.con.execute("UPDATE batch_stage SET status='done' WHERE batch_id=?", (b,))
        for _ in range(invariants.ESCALATE):
            r = invariants.tend(self.con)
        self.assertEqual(r["left"], 1)
        self.assertEqual(len(r["issues"]), 1)                  # an issue on the module, once
        self.assertEqual(invariants.tend(self.con)["issues"], [])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM case_event WHERE batch_id=?", (b,)).fetchone()[0], 0)
        notes = self.con.execute("SELECT COUNT(*) FROM notification WHERE key LIKE 'state%'").fetchone()[0]
        self.assertEqual(notes, 0)                             # nothing for the user's board

    def test_dead_process_row(self):
        b = self.batch("experiment", "s1")
        with tx(self.con):
            self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, state, attempt, started)"
                             " VALUES (?, 's1', 999999, 999999, 'x', '/', '/dev/null', 'running', 1, '2026-10-23T00:00:00')", (b,))
        self.assertTrue(any(i["check"] == "process" for i in invariants.run(self.con)))
        invariants.tend(self.con)
        self.assertEqual(self.con.execute("SELECT state FROM process WHERE batch_id=?", (b,)).fetchone()[0], "lost")


if __name__ == "__main__":
    unittest.main()
