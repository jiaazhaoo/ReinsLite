"""[run] work_dirs: a project can say where batch work dirs live; {batch} in --work-dir becomes the batch id."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches
from reins.store import ReinsError, connect

STAGES = [{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}]


class WorkDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-wd-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        (self.tmp / "data/town/runs").mkdir(parents=True)
        (self.tmp / "data/benchmarks/b1/experiments").mkdir(parents=True)
        self.patterns = [str(self.tmp / "data/*/runs/{batch}"), str(self.tmp / "data/benchmarks/*/experiments/{batch}")]

    def open(self, work_dir, patterns="default"):
        return batches.open_(self.con, project="p", scope="town-wp3", type_="experiment", purpose="t",
                             case_file=self.tmp / "cases.txt", stages=STAGES, work_dir=work_dir,
                             work_dir_patterns=self.patterns if patterns == "default" else patterns)

    def work_dir(self, b):
        return self.con.execute("SELECT work_dir FROM batch WHERE batch_id=?", (b,)).fetchone()[0]

    def test_batch_placeholder_is_filled_and_folder_created(self):
        b = self.open(str(self.tmp / "data/town/runs/{batch}"))
        self.assertEqual(self.work_dir(b), str(self.tmp / "data/town/runs" / b))
        self.assertTrue((self.tmp / "data/town/runs" / b).is_dir())

    def test_benchmark_experiment_folder_is_allowed(self):
        b = self.open(str(self.tmp / "data/benchmarks/b1/experiments/{batch}"))
        self.assertTrue(self.work_dir(b).endswith("/experiments/" + b))

    def test_folder_outside_the_patterns_is_refused_and_nothing_is_recorded(self):
        with self.assertRaises(ReinsError) as e:
            self.open(str(self.tmp / "data/town/wp3-e2e-v2"))
        self.assertIn("runs/", str(e.exception))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM batch").fetchone()[0], 0)

    def test_a_name_other_than_the_batch_id_is_refused(self):
        with self.assertRaises(ReinsError):
            self.open(str(self.tmp / "data/town/runs/my-test"))

    def test_star_never_crosses_a_slash(self):
        with self.assertRaises(ReinsError):
            self.open(str(self.tmp / "data/town/extra/runs/{batch}"))

    def test_missing_parent_is_refused(self):
        with self.assertRaises(ReinsError):
            self.open(str(self.tmp / "data/other/runs/{batch}"))

    def test_without_patterns_anything_outside_code_is_accepted(self):
        b = self.open(str(self.tmp / "anywhere"), patterns=None)
        self.assertEqual(self.work_dir(b), str(self.tmp / "anywhere"))


if __name__ == "__main__":
    unittest.main()
