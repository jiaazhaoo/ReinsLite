"""Batch and stage status have one writer (reins/state.py), with its transition table and guards (2026-10-23)."""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from reins import batches, state
from reins.store import ReinsError, connect, tx

SRC = Path(__file__).resolve().parents[1] / "reins"
WRITE = re.compile(r"UPDATE\s+batch(?:_stage)?\s+SET\s+(?:(?!WHERE)[^\"'])*?\bstatus\s*=", re.S)


def _stages(*names):
    return [{"stage": n, "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None} for n in names]


class OneWriter(unittest.TestCase):
    def test_no_other_module_writes_status(self):
        bad = []
        for f in sorted(SRC.glob("*.py")):
            if f.name == "state.py":
                continue
            for m in WRITE.finditer(f.read_text(encoding="utf-8")):
                bad.append(f"{f.name}: {m.group(0)[:80]}")
        self.assertEqual(bad, [], "batch / batch_stage status is written only by reins/state.py")


class Transitions(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-state-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="t",
                               case_file=self.tmp / "cases.txt", stages=_stages("s1", "s2"))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_stage_done_needs_every_case(self):
        with tx(self.con):
            state.stage_to(self.con, self.b, "s1", "running")
        with self.assertRaises(ReinsError), tx(self.con):
            state.stage_to(self.con, self.b, "s1", "done")
        with tx(self.con):
            state.no_per_case(self.con, self.b, "test")
            state.stage_to(self.con, self.b, "s1", "done")      # a run that records nothing per case may end
        self.assertEqual(self.con.execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage='s1'", (self.b,)).fetchone()[0], "done")

    def test_batch_done_needs_every_stage_and_done_is_final(self):
        with self.assertRaises(ReinsError), tx(self.con):
            state.batch_to(self.con, self.b, "done")
        with tx(self.con):
            state.no_per_case(self.con, self.b, "test")
            for s in ("s1", "s2"):
                state.stage_to(self.con, self.b, s, "skipped", "t")
            self.assertTrue(state.maybe_done(self.con, self.b))
        with self.assertRaises(ReinsError), tx(self.con):
            state.batch_to(self.con, self.b, "running")
        with self.assertRaises(ReinsError), tx(self.con):
            state.stage_to(self.con, self.b, "s1", "running")

    def test_every_change_is_an_event(self):
        with tx(self.con):
            state.batch_to(self.con, self.b, "paused", "why not", reason="why not")
            state.batch_to(self.con, self.b, "running", "back", reason=None)
        ev = [r[0] for r in self.con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='state' ORDER BY id", (self.b,))]
        self.assertEqual(ev, ["batch open -> paused (why not)", "batch paused -> running (back)"])
        self.assertIsNone(self.con.execute("SELECT status_reason FROM batch WHERE batch_id=?", (self.b,)).fetchone()[0])

    def test_account_fills_from_results_then_skips(self):
        with tx(self.con):
            state.stage_to(self.con, self.b, "s1", "running")
            self.assertEqual(state.account(self.con, self.b, "s1", {"101"}, "no outcome"), (1, 1))
            state.stage_to(self.con, self.b, "s1", "done")
        got = dict(self.con.execute("SELECT case_id, status FROM case_current WHERE batch_id=? AND stage='s1'", (self.b,)).fetchall())
        self.assertEqual(got, {"101": "done", "102": "skipped"})


if __name__ == "__main__":
    unittest.main()


class Legacy(unittest.TestCase):
    def test_legacy_hidden_but_working(self):
        import io, contextlib
        from reins import cli
        p = cli.build_parser()
        cli._hide_legacy(p)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            p.parse_args(["batch", "--help"])
        self.assertNotIn("skip-cases", buf.getvalue())
        self.assertIn("skip-stage", buf.getvalue())
        a = p.parse_args(["spend", "weekly"])                     # still parses
        self.assertEqual((a.cmd, a.sub), ("spend", "weekly"))
