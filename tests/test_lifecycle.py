"""A batch reaches an end (2026-10-22: 68 batches open, owners all gone; 39 were scripts that exited 0 and reported
no case outcome, 9 were left paused by the disk guard)."""
from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from reins import batches, inbox, lifecycle, runner
from reins.store import connect, now, tx

ROOT = Path(__file__).resolve().parents[1]


def ago(**kw):
    return (dt.datetime.now() - dt.timedelta(**kw)).isoformat(timespec="seconds")


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "PYTHONPATH")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-lc-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        os.environ["PYTHONPATH"] = str(ROOT)
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, type_="experiment", status=None, idle_days=0.0):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="lifecycle",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 0,
                                                                     "spend_cap": None, "time_limit_s": None}])
        with tx(self.con):
            self.con.execute("UPDATE batch SET type=?, preflight_ok=1 WHERE batch_id=?", (type_, b))
        if status:
            at = ago(days=idle_days)
            with tx(self.con):
                self.con.execute("UPDATE batch SET status=?, created=? WHERE batch_id=?", (status, at, b))
                self.con.execute("UPDATE batch_event SET at=? WHERE batch_id=?", (at, b))
        return b

    def wait_for(self, cond, secs=20):
        for _ in range(int(secs / 0.1)):
            if cond():
                return True
            time.sleep(0.1)
        return False

    def test_a_script_that_reports_no_cases_ends_on_exit_0(self):
        b = self.batch()
        runner.start(self.con, b, "s", [sys.executable, "-c", "print('ceiling test done')"], self.tmp, None, 0)
        self.assertTrue(self.wait_for(lambda: batches.get(self.con, b)["status"] == "done"))
        note = self.con.execute("SELECT note FROM batch_stage WHERE batch_id=?", (b,)).fetchone()[0]
        self.assertIn("no per-case outcomes", note)

    def test_production_waits_for_a_person_and_the_owner_is_told(self):
        b = self.batch("production")
        runner.start(self.con, b, "s", [sys.executable, "-c", "pass"], self.tmp, None, 0)
        self.assertTrue(self.wait_for(lambda: inbox.unread(self.con, "owner")))
        self.assertNotEqual(batches.get(self.con, b)["status"], "done")

    def test_sweep_finishes_closes_and_leaves_production(self):
        finished = self.batch(status="paused")
        batches.stage_start(self.con, finished, "s")
        with tx(self.con):
            self.con.execute("UPDATE batch SET status='paused', status_reason='process gone: it ended normally (exit 0)' WHERE batch_id=?", (finished,))
            self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, state, exit_code, started, ended)"
                             " VALUES (?, 's', 999999, 999999, 'x', '/', '/x', 'exited', 0, ?, ?)", (finished, ago(days=2), ago(days=2)))
        forgotten = self.batch(status="paused", idle_days=9)
        prod = self.batch("rework", status="paused", idle_days=9)
        out = lifecycle.sweep(self.con)
        self.assertEqual(batches.get(self.con, finished)["status"], "done")
        self.assertEqual(batches.get(self.con, forgotten)["status"], "closed")
        self.assertEqual(batches.get(self.con, prod)["status"], "paused")          # production ends by a person
        self.assertIn("closed 1 stale batch(es)", out)

    def test_disk_pause_is_undone_when_space_returns(self):
        b = self.batch(status="running")
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            with tx(self.con):
                self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, state, started)"
                                 " VALUES (?, 's', ?, ?, 'x', '/', '/x', 'running', ?)", (b, p.pid, p.pid, now()))
            runner.ctl(self.con, "pause", b, "disk /data at 91%", system=True)
            self.assertEqual(batches.get(self.con, b)["status"], "paused")
            (self.tmp / "home" / "config.toml").write_text("disk_pause_pct = 100\n")   # the disk is fine again
            out = lifecycle.resume_after_disk(self.con)
            self.assertEqual(len(out), 1)
            self.assertEqual(batches.get(self.con, b)["status"], "running")
        finally:
            os.killpg(p.pid, signal.SIGKILL)

    def test_hook_asks_once_about_loose_batches(self):
        b = self.batch(status="paused")
        env = {**os.environ}

        def stop():
            r = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_hook.py")], capture_output=True, text=True,
                               input=json.dumps({"hook_event_name": "Stop", "session_id": "owner"}), env=env, timeout=60)
            return json.loads(r.stdout) if r.stdout.strip() else {}
        first = stop()
        self.assertEqual(first.get("decision"), "block")
        self.assertIn(b, first["reason"])
        self.assertEqual(stop(), {})


if __name__ == "__main__":
    unittest.main()
