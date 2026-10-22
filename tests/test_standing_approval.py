"""A standing approval (config [standing] TYPE = USD, a recorded decision) gives every new batch of that type with a
paid stage that cap at open, through the normal pool checks. User 2026-10-22: intake batches may spend up to $1 each
reading capture rules without asking each time."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, settings_edit
from reins.store import ReinsError, connect


def _stage(paid):
    return [{"stage": "s", "module_version": None, "paid": paid, "spend_cap": None, "time_limit_s": None}]


class Standing(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-st-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "home").mkdir()
        (self.tmp / "home" / "config.toml").write_text("[pool]\ndaily_cap = 3.0\nmax_batch_cap = 50.0\n")
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def open(self, type_="intake", paid=1):
        self.n += 1
        return batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_=type_, purpose="t",
                             case_file=self.tmp / "cases.txt", stages=_stage(paid))

    def cap(self, b):
        return batches.get(self.con, b)["spend_cap"]

    def test_set_and_applied(self):
        self.assertEqual(self.cap(self.open()), 0.0)                       # nothing standing yet
        r = settings_edit.set_(self.con, "standing.intake", "1", why="user agreed")
        self.assertTrue(r["decision"])
        b = self.open()
        self.assertEqual(self.cap(b), 1.0)
        ev = [r[0] for r in self.con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='spend_approved'", (b,))]
        self.assertTrue(any("standing" in e for e in ev))
        self.assertEqual(self.cap(self.open(paid=0)), 0.0)                 # no paid stage: no cap
        self.assertEqual(self.cap(self.open(type_="experiment")), 0.0)     # other types untouched

    def test_pool_refusal_is_an_event_not_an_error(self):
        settings_edit.set_(self.con, "standing.intake", "1", why="user agreed")
        caps = [self.cap(self.open()) for _ in range(4)]                    # $3 a day: the 4th does not fit
        self.assertEqual(caps, [1.0, 1.0, 1.0, 0.0])
        b = f"x-wp{self.n}"
        n = self.con.execute("SELECT COUNT(*) FROM batch_event WHERE event='standing_refused'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_bad_keys(self):
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "standing.nosuchtype", "1", why="x")
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "standing.intake", "500", why="x")    # over max_batch_cap


if __name__ == "__main__":
    unittest.main()
