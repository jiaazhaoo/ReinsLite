"""A batch that stops is known at once; one killed for memory goes on by itself (2026-11-04: 4 h unnoticed)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import batches, inbox, runner, state, stops
from reins.store import connect, tx

OWNER = "aaaa1111-0000"


class Stops(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "REINS_SUPERVISOR")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-stops-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = OWNER
        os.environ.pop("REINS_SUPERVISOR", None)
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="t", case_file=self.tmp / "cases.txt",
                               stages=[{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}],
                               work_dir=str(self.tmp / "runs" / "{batch}"))
        with tx(self.con):
            self.con.execute("UPDATE batch SET owner_session=? WHERE batch_id=?", (OWNER, self.b))
            state.batch_to(self.con, self.b, "running", "test start")
        stops.left_running(self.con)                                     # first pass only sets the mark

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def kill(self, as_="supervisor-0000"):
        os.environ["REINS_SESSION"] = as_
        with tx(self.con):
            batches._event(self.con, self.b, "killed", "pid 42 (python x) SIGKILL")
            state.batch_to(self.con, self.b, "paused", "killed", reason="killed: pid 42 rc -9")
        os.environ["REINS_SESSION"] = OWNER

    def test_wait_returns_on_pause(self):
        self.kill()
        r = runner.wait(self.con, self.b, timeout_s=5)
        self.assertEqual((r["status"], r["timed_out"]), ("paused", False))

    def test_leaving_running_tells_board_and_owner(self):
        self.kill()
        self.assertEqual(stops.left_running(self.con), [f"{self.b}: left running -> paused"])
        n = self.con.execute("SELECT COUNT(*) FROM notification WHERE key LIKE ? AND level='action'", (f"left-running:{self.b}:%",)).fetchone()[0]
        self.assertEqual(n, 1)
        msgs = inbox.unread(self.con, OWNER)
        self.assertTrue(msgs and "stopped (paused)" in msgs[0]["title"])
        self.assertEqual(stops.left_running(self.con), [])               # once

    def test_owner_pausing_itself_is_not_news(self):
        with tx(self.con):
            state.batch_to(self.con, self.b, "paused", "paused by request")
        self.assertEqual(stops.left_running(self.con), [])

    def test_auto_resume_restarts_then_gives_up(self):
        with tx(self.con):
            self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, session, state, started)"
                             " VALUES (?, '*', 999999, 999999, 'tools/run.sh /x y', ?, '/dev/null', ?, 'exited', '2026-11-04T04:00:00')",
                             (self.b, str(self.tmp), OWNER))
        self.kill()
        cfg = {"after_kill": True, "wait_min": 0, "mem_free_pct": 10, "max": 2}
        calls = []
        with mock.patch.object(stops, "_cfg", return_value=cfg), mock.patch.object(runner, "ctl", return_value="ok"), \
                mock.patch.object(runner, "start", side_effect=lambda *a, **k: calls.append((a, os.environ.get("REINS_SESSION"))) or {"log": "l"}):
            self.assertEqual(stops.auto_resume(self.con, mem_free_pct=5), [])          # memory not back yet
            out = stops.auto_resume(self.con, mem_free_pct=60)
            self.assertIn("auto resumed (1/2)", out[0])
            self.assertEqual(calls[0][0][2:4], ("*", ["tools/run.sh", "/x", "y"]))
            self.assertEqual(calls[0][1], OWNER)                                       # started as the owner
            stops.auto_resume(self.con, mem_free_pct=60)                               # (still paused in this test) 2/2
            self.assertEqual(len(calls), 2)
            self.assertEqual(stops.auto_resume(self.con, mem_free_pct=60), [])         # gave up
        self.assertEqual(len(calls), 2)
        gave = self.con.execute("SELECT COUNT(*) FROM notification WHERE key LIKE 'auto-resume-gave-up:%'").fetchone()[0]
        self.assertEqual(gave, 1)

    def test_not_opted_in_stays_paused(self):
        self.kill()
        with mock.patch.object(stops, "_cfg", return_value={}), mock.patch.object(runner, "start") as st:
            self.assertEqual(stops.auto_resume(self.con, mem_free_pct=90), [])
            st.assert_not_called()


if __name__ == "__main__":
    unittest.main()
