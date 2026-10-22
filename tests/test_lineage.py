"""One piece of work, many runs: a batch knows the earlier run it repeats, splits or extends, and a production run
with nothing alive is settled by its results and its later runs -- the person only starts runs (user 2026-10-22)."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, lineage, projects
from reins.store import connect, tx

TOML = '[project]\n'


class Lineage(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "REINS_CODE_ROOT")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-lin-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        code = self.tmp / "code" / "proj"; (code / ".git").mkdir(parents=True)
        (code / "reins.toml").write_text('project = "p"\n[results]\nfile = "res.csv"\nkey = "case"\nvalue = "lane"\n')
        os.environ["REINS_CODE_ROOT"] = str(self.tmp / "code")
        projects._CACHE.clear()
        self.con = connect(self.tmp / "home")
        self.n = 0

    def tearDown(self):
        projects._CACHE.clear()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, cases, status="paused", results=(), idle_days=0.0, minutes=0):
        self.n += 1
        f = self.tmp / f"c{self.n}.txt"; f.write_text("\n".join(cases) + "\n")
        wd = self.tmp / f"wd{self.n}"; wd.mkdir()
        (wd / "res.csv").write_text("case,lane\n" + "".join(f"{c},auto\n" for c in results))
        b = batches.open_(self.con, project="p", scope="town-wp1", type_="experiment", purpose=f"run {self.n}", case_file=f,
                          stages=[{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}],
                          work_dir=str(wd))
        at = (dt.datetime(2026, 10, 1) + dt.timedelta(minutes=self.n * 10 + minutes)).isoformat(timespec="seconds")
        old = (dt.datetime.now() - dt.timedelta(days=idle_days)).isoformat(timespec="seconds")
        with tx(self.con):
            self.con.execute("UPDATE batch SET status=?, created=? WHERE batch_id=?", (status, at, b))
            self.con.execute("UPDATE batch_event SET at=? WHERE batch_id=?", (old, b))
        return b

    def test_relations_and_settling(self):
        first = self.batch(["1", "2", "3", "4"])
        part = self.batch(["1", "2"])                                 # a split
        again = self.batch(["1", "2", "3", "4"], status="done", results=["1", "2", "3", "4"])   # the rerun that finished
        bigger = self.batch(["1", "2", "3", "4", "5"])
        for b in (part, again, bigger):
            lineage.relate(self.con, b)
        rel = {b: self.con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='lineage'", (b,)).fetchone()[0]
               for b in (part, again, bigger)}
        self.assertTrue(rel[part].startswith("part of"))
        self.assertTrue(rel[again].startswith("rerun of"))
        self.assertTrue(rel[bigger].startswith("extends"))
        self.assertIn("superseded", lineage.settle(self.con, first))         # later runs cover its cases
        exp = self.batch(["1", "2", "3", "4", "5", "6"])
        with tx(self.con):
            self.con.execute("UPDATE batch SET type='rework' WHERE batch_id=?", (bigger,))
            self.con.execute("UPDATE batch SET type='experiment' WHERE batch_id=?", (exp,))
        self.assertIsNone(lineage.superseded_by(self.con, batches.get(self.con, bigger)))   # an experiment never replaces it
        self.assertEqual(batches.get(self.con, first)["status"], "closed")

    def test_complete_partial_and_stopped(self):
        complete = self.batch(["7", "8"], results=["7", "8"])
        partial = self.batch(["9", "10"], results=["9"], idle_days=8)
        nothing = self.batch(["11"], idle_days=8)
        fresh = self.batch(["12"], idle_days=1)
        self.assertIn("results complete", lineage.settle(self.con, complete))
        self.assertIn("partial", lineage.settle(self.con, partial))
        self.assertIn("stopped", lineage.settle(self.con, nothing))
        self.assertIsNone(lineage.settle(self.con, fresh))                   # too soon to say
        self.assertEqual([batches.get(self.con, b)["status"] for b in (complete, partial, nothing)], ["done", "done", "closed"])


if __name__ == "__main__":
    unittest.main()
