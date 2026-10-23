"""The improvement loop: reviewer verdicts in, misses out as issues, dispatch never merges (2026-10-23)."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import batches, loop
from reins.store import connect, tx


class Loop(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-loop-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n103\n")
        self.wd = self.tmp / "work"
        (self.wd / "lab" / "review").mkdir(parents=True)
        (self.wd / "lab" / "qa_final.csv").write_text("case,lane\nSHF_101,auto pass\nSHF_102,auto pass\nSHF_103,human review\n")
        db = sqlite3.connect(self.wd / "lab" / "review" / "review.db")
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT, reviewer TEXT, case_id TEXT, action TEXT, verdict TEXT,"
                   " error_type TEXT, note TEXT)")
        db.executemany("INSERT INTO events (at, reviewer, case_id, action, verdict, error_type, note) VALUES (?,?,?,?,?,?,?)", [
            ("2026-10-23T01:00:00", "maggie", "SHF_101", "verdict", "error", "polygon_extent", None),   # a miss
            ("2026-10-23T01:01:00", "maggie", "SHF_102", "verdict", "correct", None, None),
            ("2026-10-23T01:02:00", "maggie", "SHF_103", "verdict", "error", "location", None),        # flagged already
            ("2026-10-23T01:03:00", "maggie", "SHF_102", "view", None, None, None)])
        db.commit(); db.close()
        self.b = batches.open_(self.con, project="p", scope="sheffield-wp3", type_="experiment", purpose="t",
                               case_file=self.tmp / "cases.txt", work_dir=str(self.wd),
                               stages=[{"stage": "check", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}])
        self.cfg = {"review_source": {"databases": ["{work_dir}/lab/review/review.db"], "where": "action = 'verdict'",
                                      "verdicts": {"correct": "correct", "error": "wrong", "query": "unsure"},
                                      "case_pattern": "([0-9]+)$"},
                    "results": {"file": "lab/qa_final.csv", "key": "case", "key_pattern": "([0-9]+)$", "value": "lane",
                                "good": ["auto pass"]},
                    "dev": {"repo": str(self.tmp), "gate": "true"}}
        with tx(self.con):                                       # a rework run (opening one needs a release)
            self.con.execute("UPDATE batch SET type='rework' WHERE batch_id=?", (self.b,))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_import_once_and_misses_become_one_issue(self):
        with mock.patch.object(loop, "_cfg", return_value=self.cfg):
            self.assertEqual(loop.import_reviews(self.con, self.b), 3)
            self.assertEqual(loop.import_reviews(self.con, self.b), 0)            # nothing twice
            self.assertEqual(loop.misses(self.con, self.b), {"polygon_extent": ["101"]})
            out = loop.tend(self.con, force=True)
            self.assertTrue(any("issue #" in o for o in out))
            self.assertEqual(loop.tend(self.con, force=True), [])                 # covered: no second issue
        n = self.con.execute("SELECT COUNT(*) FROM issue WHERE batch_id=?", (self.b,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_dispatch_never_merges(self):
        with mock.patch.object(loop, "_cfg", return_value=self.cfg):
            loop.tend(self.con, force=True)
            iid = self.con.execute("SELECT id FROM issue WHERE batch_id=?", (self.b,)).fetchone()[0]
            with mock.patch("reins.loop.config", return_value={"loop": {"agent_cmd": "echo"}}):
                msg = loop.dispatch(self.con, iid)
        self.assertIn("dispatched", msg)
        ev = [r[0] for r in self.con.execute("SELECT event FROM issue_event WHERE issue_id=?", (iid,))]
        self.assertIn("dispatched", ev)
        import time; time.sleep(0.3)
        log = (self.tmp / "home" / "loop" / f"issue-{iid}.log").read_text()
        self.assertIn("Do NOT run reins dev finish", log)


if __name__ == "__main__":
    unittest.main()
