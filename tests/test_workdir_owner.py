"""A batch's work dir belongs to it (2026-10-25: one session wrote into another's experiment dir)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, watchdog
from reins.store import ReinsError, connect, now, tx


def _stages():
    return [{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}]


class WorkDirOwner(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-wd-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        (self.tmp / "home").mkdir()
        (self.tmp / "runs").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def open(self, who, wd, **kw):
        os.environ["REINS_SESSION"] = who
        return batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="t", case_file=self.tmp / "cases.txt",
                             stages=_stages(), work_dir=wd, **kw)

    def test_another_sessions_live_dir_is_refused(self):
        wd = str(self.tmp / "runs" / "shared")
        os.makedirs(wd)
        self.open("aaaa", wd, adopt_dir=None) if False else None
        b = self.open("aaaa", wd)
        self.assertTrue((Path(wd) / batches.MARKER).exists())
        with self.assertRaises(ReinsError):
            self.open("bbbb", wd)
        self.open("aaaa", wd)                                   # the same session re-running in its own dir

    def test_a_dir_with_files_nobody_registered_is_refused_unless_adopted(self):
        wd = self.tmp / "runs" / "guessed"
        wd.mkdir()
        (wd / "config.json").write_text("{}")                   # someone's guess, made before batch open
        with self.assertRaises(ReinsError):
            self.open("cccc", str(wd))
        b = self.open("cccc", str(wd), adopt_dir="old outputs kept on purpose")
        ev = self.con.execute("SELECT detail FROM batch_event WHERE batch_id=? AND event='adopted_dir'", (b,)).fetchone()
        self.assertIn("on purpose", ev[0])

    def test_watchdog_tells_the_owner(self):
        wd = str(self.tmp / "runs" / "mine")
        os.makedirs(wd)
        b = self.open("aaaa", wd)
        with tx(self.con):
            self.con.execute("INSERT INTO session (id, started, last_seen, status) VALUES ('bbbb', ?, ?, 'active')", (now(), now()))
            self.con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?, 'bbbb', 'command', ?)",
                             (now(), f"cp config.json {wd}/config.json"))
            self.con.execute("INSERT INTO session_event (at, session_id, kind, detail) VALUES (?, 'bbbb', 'command', ?)",
                             (now(), f"ls {wd}"))                # reading only: not reported
        out = watchdog._foreign_touch(self.con)
        self.assertEqual(len(out), 1)
        self.assertEqual(watchdog._foreign_touch(self.con), [])  # once per session
        n = self.con.execute("SELECT COUNT(*) FROM session_message WHERE to_session='aaaa'").fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
