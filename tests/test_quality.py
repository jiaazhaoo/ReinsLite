"""Each release's real error rate from human and client verdicts (2026-10-23)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import batches, quality, review
from reins.store import connect, tx


class Quality(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-q-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n103\n104\n")
        self.cfg = {"results": {"file": "lab/qa_final.csv", "key": "case", "key_pattern": "([0-9]+)$", "value": "lane",
                                "good": ["auto pass"]}}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_(self, release, created, lanes):
        wd = self.tmp / release
        (wd / "lab").mkdir(parents=True)
        (wd / "lab" / "qa_final.csv").write_text("case,lane\n" + "".join(f"SHF_{c},{l}\n" for c, l in lanes.items()))
        b = batches.open_(self.con, adopt_dir='test fixture: results written first', project="p", scope="x-wp1", type_="experiment", purpose="t", case_file=self.tmp / "cases.txt",
                          work_dir=str(wd), day=created.replace("-", "")[:8],
                          stages=[{"stage": "check", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}])
        with tx(self.con):
            self.con.execute("UPDATE batch SET type='rework', release_name=?, created=? WHERE batch_id=?", (release, created, b))
        return b

    def verdicts(self, b, rows):
        review.record(self.con, b, [{"case_id": c, "reviewer": who, "action": "verdict", "verdict": v, "at": at,
                                     "error_type": "x" if v == "wrong" else None} for c, who, v, at in rows], review.taxonomy(None))

    def test_verdict_goes_to_the_version_that_produced_it(self):
        b1 = self.run_("qa-20261001-1", "2026-10-01T10:00:00", {"101": "auto pass", "102": "auto pass", "103": "human review", "104": "auto pass"})
        b2 = self.run_("qa-20261010-1", "2026-10-10T10:00:00", {"101": "auto pass", "102": "auto pass", "103": "auto pass", "104": "auto pass"})
        self.verdicts(b2, [("101", "maggie", "wrong", "2026-10-05T09:00:00"),       # judged v1's output
                           ("102", "client:HMLR", "correct", "2026-10-05T09:00:00"),
                           ("103", "maggie", "wrong", "2026-10-05T09:00:00"),       # v1 sent it to a person: a good flag
                           ("102", "maggie", "correct", "2026-10-12T09:00:00"),     # judged v2's output
                           ("104", "client:LMK", "correct", "2026-10-12T09:00:00"),
                           ("101", "maggie", "correct", "2026-10-12T09:00:00")])
        with mock.patch("reins.production._cfg", return_value=self.cfg):
            q = quality.compute(self.con, "p")
        r = {x["release"]: x for x in q["releases"]}
        self.assertEqual((r["qa-20261001-1"]["checked_auto"], r["qa-20261001-1"]["wrong_auto"]), (2, 1))
        self.assertEqual((r["qa-20261001-1"]["checked_human"], r["qa-20261001-1"]["wrong_human"]), (1, 1))
        self.assertEqual((r["qa-20261010-1"]["checked_auto"], r["qa-20261010-1"]["wrong_auto"]), (3, 0))
        self.assertEqual(r["qa-20261010-1"]["client_checked"], 1)
        self.assertTrue(r["qa-20261010-1"]["low_n"])
        self.assertEqual([x["release"] for x in q["releases"]], ["qa-20261001-1", "qa-20261010-1"])


if __name__ == "__main__":
    unittest.main()
