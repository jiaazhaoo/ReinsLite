"""The production page: production and rework only, one block per work package; a case's result comes from the newest
batch that has a row for it, rows of cases outside the batch do not count, and the stopped-but-all-results package is
not called complete."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, production, projects
from reins.store import connect, tx

TOML = '''project = "p"
[results]
file = "out/res.csv"
key = "case"
key_pattern = "([0-9]+)$"
value = "lane"
detail = "cat"
good = ["auto"]
[[results.reasons]]
match = "no scans"
zh = "没有扫描件"
en = "No scans"
[labels]
auto = { zh = "自动通过", en = "Auto-passed" }
'''


class Production(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_CODE_ROOT", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-pr-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        code = self.tmp / "code" / "proj"
        (code / ".git").mkdir(parents=True)
        (code / "reins.toml").write_text(TOML, encoding="utf-8")
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

    def batch(self, scope, type_, status, cases, rows=None, created="2026-10-10T10:00:00"):
        self.n += 1
        cf = self.tmp / f"cases{self.n}.txt"
        cf.write_text("\n".join(cases) + "\n")
        wd = self.tmp / f"wd{self.n}"
        b = batches.open_(self.con, project="p", scope=scope, type_="experiment", purpose=f"part {self.n}", case_file=cf,
                          stages=[{"stage": s, "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}
                                  for s in ("prep", "check")])
        new = f"{scope}-{type_}-20261010-{self.n}"           # the strict types need a release to open: rename the experiment
        self.con.execute("PRAGMA foreign_keys=OFF")
        with tx(self.con):
            for t in ("batch_case", "batch_stage", "batch_event"):
                self.con.execute(f"UPDATE {t} SET batch_id=? WHERE batch_id=?", (new, b))
            self.con.execute("UPDATE batch SET preflight_ok=1, batch_id=?, type=?, seq=?, status=?, work_dir=?, created=? WHERE batch_id=?",
                             (new, type_, self.n, status, str(wd), created, b))
        self.con.execute("PRAGMA foreign_keys=ON")
        b = new
        if rows is not None:
            (wd / "out").mkdir(parents=True)
            (wd / "out" / "res.csv").write_text("case,lane,cat\n" + "".join(f"X_{c},{l},{d}\n" for c, l, d in rows))
        return b

    def test_packages_results_and_status(self):
        self.batch("a-wp1", "production", "done", ["1", "2", "3"],
                   [("1", "auto", ""), ("2", "human", "c1"), ("3", "human", "c2"), ("9", "auto", "")])   # 9 is not in the batch
        self.batch("a-wp1", "rework", "done", ["3"], [("3", "auto", "")], created="2026-10-11T10:00:00")  # newer: 3 is now auto
        self.batch("a-wp1", "rework", "failed", ["1"], [("1", "human", "c1")], created="2026-10-12T10:00:00")  # superseded: ignored
        self.batch("b-wp2", "experiment", "running", ["5"], [("5", "auto", "")])                          # not production
        b = self.batch("c-wp3", "production", "paused", ["7", "8"], [("7", "auto", "")])
        batches.stage_start(self.con, b, "prep")
        batches.mark(self.con, b, "prep", [("8", "skipped", "no scans")])
        with tx(self.con):
            self.con.execute("UPDATE batch SET status='paused', status_reason='disk /data at 90%' WHERE batch_id=?", (b,))
        v = production.view(self.con)
        pk = {p["scope"]: p for p in v["packages"]}
        self.assertEqual(set(pk), {"a-wp1", "c-wp3"})
        a = pk["a-wp1"]
        self.assertEqual((a["n_cases"], a["with_result"], a["good"], a["status"]), (3, 3, 2, "done"))
        self.assertEqual({r["value"]: r["n"] for r in a["results"]}, {"auto": 2, "human": 1})
        self.assertEqual(len(a["parts"]), 2)
        c = pk["c-wp3"]
        self.assertEqual((c["with_result"], c["status"], c["pause"]), (1, "paused", "disk_full"))
        self.assertEqual(c["why"], [{"word": "r0", "n": 1}])
        self.assertEqual(v["labels"]["p"]["auto"]["en"], "Auto-passed")
        self.assertEqual(v["reasons"]["p"][0]["en"], "No scans")
        self.assertEqual(v["totals"]["packages"], 2)

    def test_all_results_but_a_paused_batch_is_not_complete(self):
        self.batch("d-wp4", "production", "paused", ["1", "2"], [("1", "auto", ""), ("2", "human", "c1")])
        p = production.view(self.con)["packages"][0]
        self.assertEqual(p["status"], "results_in")


if __name__ == "__main__":
    unittest.main()
