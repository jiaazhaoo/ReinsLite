"""The ops panel: delivery batches only; why a batch is not moving; a forecast from the batch's own pace or earlier
delivery batches' time per case; a stage left open on a paused batch is shown stalled, not running; our reviewers and the
client's feedback counted apart; alerts about other batches left out."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, ops, projects
from reins.store import connect, tx

TOML = '''project = "p"
[results]
file = "out/res.csv"
key = "case"
value = "lane"
good = ["auto"]
'''


def iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds")


class OpsPanel(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_CODE_ROOT", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ops-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        code = self.tmp / "code" / "proj"
        (code / ".git").mkdir(parents=True)
        (code / "reins.toml").write_text(TOML, encoding="utf-8")
        os.environ["REINS_CODE_ROOT"] = str(self.tmp / "code")
        projects._CACHE.clear()
        self.con = connect(self.tmp / "home")
        self.n = 0
        self.now = dt.datetime.now()

    def tearDown(self):
        projects._CACHE.clear()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, scope, type_, status, n_cases, stages=("prep", "check"), paid=(), cap=0.0, reason=None):
        self.n += 1
        cases = [str(i) for i in range(1, n_cases + 1)]
        cf = self.tmp / f"cases{self.n}.txt"
        cf.write_text("\n".join(cases) + "\n")
        wd = self.tmp / f"wd{self.n}"
        b = batches.open_(self.con, project="p", scope=scope, type_="experiment", purpose=f"part {self.n}", case_file=cf,
                          stages=[{"stage": s, "module_version": None, "paid": int(s in paid), "spend_cap": None,
                                   "time_limit_s": None} for s in stages])
        new = f"{scope}-{type_}-20261027-{self.n}"            # delivery types need a release to open: rename an experiment
        self.con.execute("PRAGMA foreign_keys=OFF")
        with tx(self.con):
            for t in ("batch_case", "batch_stage", "batch_event"):
                self.con.execute(f"UPDATE {t} SET batch_id=? WHERE batch_id=?", (new, b))
            self.con.execute("UPDATE batch SET preflight_ok=1, batch_id=?, type=?, seq=?, status=?, status_reason=?, "
                             "work_dir=?, spend_cap=?, created=? WHERE batch_id=?",
                             (new, type_, self.n, status, reason, str(wd), cap, iso(self.now), b))
        self.con.execute("PRAGMA foreign_keys=ON")
        return new

    def stage(self, b, stage, status, started=None, ended=None):
        with tx(self.con):
            self.con.execute("UPDATE batch_stage SET status=?, started=?, ended=? WHERE batch_id=? AND stage=?",
                             (status, started and iso(started), ended and iso(ended), b, stage))

    def done(self, b, stage, cases):
        with tx(self.con):
            for c in cases:
                self.con.execute("INSERT INTO case_event (at, batch_id, stage, case_id, status) VALUES (?,?,?,?, 'done')",
                                 (iso(self.now), b, stage, c))

    def by_id(self):
        return {x["batch_id"]: x for x in ops.view(self.con)["batches"]}

    def test_only_delivery_batches_and_why_waiting(self):
        cap = self.batch("a-wp1", "production", "open", 10, paid=("check",))
        exp = self.batch("a-wp1", "experiment", "running", 10)
        v = self.by_id()
        self.assertIn(cap, v)
        self.assertNotIn(exp, v)
        self.assertEqual(v[cap]["waiting"], "waiting for a spend cap (paid stages)")
        self.assertIsNone(v[cap]["forecast_finish"])

    def test_forecast_from_own_pace_and_history(self):
        old = self.batch("h-wp0", "production", "done", 100)            # history: check took 2 s per case
        self.stage(old, "prep", "done", self.now - dt.timedelta(days=2, hours=1), self.now - dt.timedelta(days=2))
        self.stage(old, "check", "done", self.now - dt.timedelta(days=2), self.now - dt.timedelta(days=2) + dt.timedelta(seconds=200))
        b = self.batch("a-wp1", "rework", "running", 20)
        self.stage(b, "prep", "running", self.now - dt.timedelta(seconds=100))
        self.done(b, "prep", [str(i) for i in range(1, 11)])            # 10 of 20 in 100 s: 10 s per case
        x = self.by_id()[b]
        kinds = [s["kind"] for s in x["segments"]]
        self.assertEqual(kinds, ["running", "forecast", "planned"])
        finish = dt.datetime.fromisoformat(x["forecast_finish"])
        expect = self.now + dt.timedelta(seconds=10 * 10 + 2 * 20)     # 10 cases left at 10 s, then check at 2 s x 20
        self.assertLess(abs((finish - expect).total_seconds()), 5)
        self.assertIsNone(x["waiting"])

    def test_paused_batch_stage_is_stalled_not_running(self):
        b = self.batch("a-wp1", "production", "paused", 5, reason="process gone")
        self.stage(b, "prep", "running", self.now - dt.timedelta(days=3))
        x = self.by_id()[b]
        self.assertEqual([s["kind"] for s in x["segments"]], ["stalled"])
        self.assertEqual(x["waiting"], "paused: process gone")
        self.assertIsNone(x["forecast_finish"])

    def test_review_counts_ours_and_client_apart(self):
        b = self.batch("a-wp1", "production", "done", 4)
        wd = self.tmp / f"wd{self.n}" / "out"
        wd.mkdir(parents=True)
        (wd / "res.csv").write_text("case,lane\n1,auto\n2,human\n3,human\n4,human\n")
        with tx(self.con):
            for c, who, verdict in (("2", "jia", "wrong"), ("3", "jia", "correct"), ("4", "client:HMLR", "wrong")):
                self.con.execute("INSERT INTO review_event (at, batch_id, case_id, reviewer, action, verdict) "
                                 "VALUES (?,?,?,?, 'verdict', ?)", (iso(self.now), b, c, who, verdict))
        r = self.by_id()[b]["review"]
        self.assertEqual((r["need_person"], r["judged"], r["wrong"], r["wrong_share"]), (3, 2, 1, 0.5))
        self.assertEqual((r["client_judged"], r["client_wrong"]), (1, 1))
        self.assertEqual(r["reviewers"], ["jia"])

    def test_alerts_about_other_batches_left_out(self):
        b = self.batch("a-wp1", "production", "running", 3)
        with tx(self.con):
            for title, level in ((f"{b}: stage check failed", "warn"), ("openrouter balance $4", "action"),
                                 ("x-wp9-experiment-20261027-9 paused", "action")):
                self.con.execute("INSERT INTO notification (at, key, level, title, body) VALUES (?,?,?,?, '')",
                                 (iso(self.now), title, level, title))
        titles = [n["title"] for n in ops.view(self.con)["notices"]]
        self.assertIn(f"{b}: stage check failed", titles)
        self.assertIn("openrouter balance $4", titles)
        self.assertNotIn("x-wp9-experiment-20261027-9 paused", titles)


if __name__ == "__main__":
    unittest.main()
