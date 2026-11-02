"""reins find: one id (batch, case, path, issue, session) -> what it links to (user 2026-11-02: 索引系统)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, find
from reins.store import connect


class Find(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-find-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "abcd1234-0000"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("104977\n104978\n")
        self.wd = str(self.tmp / "runs" / "b1")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="t",
                               case_file=self.tmp / "cases.txt",
                               stages=[{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}],
                               work_dir=self.wd)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_kinds(self):
        self.assertEqual(find.kind_of(self.con, self.b), ("batch", self.b))
        self.assertEqual(find.kind_of(self.con, "SHF_104977"), ("case", "104977"))   # project prefix stripped
        self.assertEqual(find.kind_of(self.con, self.wd + "/lab/qa_final.csv")[0], "path")

    def test_batch_and_path_link_back(self):
        d = find.find(self.con, self.b)
        self.assertEqual(d["kind"], "batch")
        self.assertEqual(d["cases"], 2)
        self.assertIn("spend", d)
        p = find.find(self.con, self.wd + "/lab/qa_final.csv")
        self.assertEqual([h["batch_id"] for h in p["batches"]], [self.b])
        self.assertTrue(find.text(d))

    def test_case_lists_its_runs(self):
        d = find.find(self.con, "104977")
        self.assertEqual(d["kind"], "case")
        self.assertIn(f"experiment:{self.b}", d["other_runs"])          # experiments listed, production runs detailed
        self.assertTrue(find.text(d))


if __name__ == "__main__":
    unittest.main()
