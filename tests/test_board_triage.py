"""The board groups live batches by what they need: moving / needs you / stuck / long idle, each with a command."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, board
from reins.store import ReinsError, connect, now, tx


class Triage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-tr-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.n = 0

    def batch(self, type_, status, reason="", idle_h=0.0):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose=f"triage {self.n}",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 0,
                                                                     "spend_cap": None, "time_limit_s": None}])
        import datetime as dt
        at = (dt.datetime.now() - dt.timedelta(hours=idle_h)).isoformat(timespec="seconds")
        with tx(self.con):
            self.con.execute("UPDATE batch SET type=?, status=?, status_reason=?, created=? WHERE batch_id=?", (type_, status, reason, at, b))
            self.con.execute("UPDATE batch_event SET at=? WHERE batch_id=?", (at, b))
        return b

    def progress(self, b, idle_h):
        import datetime as dt
        at = (dt.datetime.now() - dt.timedelta(hours=idle_h)).isoformat(timespec="seconds")
        batches.stage_start(self.con, b, "s")
        with tx(self.con):
            self.con.execute("INSERT INTO case_event (at, batch_id, stage, case_id, status) VALUES (?,?, 's', '101', 'done')", (at, b))

    def test_groups_and_commands(self):
        moving = self.batch("experiment", "running", idle_h=0.2)
        self.progress(moving, 0.1)                                                   # a new case outcome: moving
        paperwork = self.batch("experiment", "running", idle_h=0.1)                  # only a batch event: not moving
        disk = self.batch("production", "paused", "disk /data at 90%", idle_h=50)
        old = self.batch("experiment", "paused", "harness bug", idle_h=80)
        stuck = self.batch("rework", "running", idle_h=5)
        forgotten = self.batch("eval", "running", idle_h=100)
        g = {c["batch_id"]: c for c in board.running_cards(self.con)}
        self.assertEqual(g[moving]["group"], "active")
        self.assertNotEqual(g[paperwork]["group"], "active")
        self.assertEqual((g[disk]["group"], g[disk]["kind"]), ("decide", "disk"))       # production stays a decision
        self.assertIn("reins ctl resume", g[disk]["cmd"])
        self.assertEqual(g[old]["group"], "stale")                                      # an old paused experiment
        self.assertEqual(g[stuck]["group"], "stuck")
        self.assertEqual(g[forgotten]["group"], "stale")
        self.assertIn("abandon", g[forgotten]["cmd"])

    def test_dead_process_is_named_with_its_resume_command(self):
        b = self.batch("rework", "running", idle_h=3)
        with tx(self.con):
            self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, session, state, attempt,"
                             " started) VALUES (?, '*', 999999, 999998, 'tools/run.sh /data/x', '/tmp', '/tmp/l', 'owner', 'running', 1,"
                             " '2020-01-01T00:00:00')", (b,))
        c = {x["batch_id"]: x for x in board.running_cards(self.con)}[b]
        self.assertEqual(c["group"], "decide"); self.assertIn(c["kind"], ("reboot", "lost"))
        self.assertEqual(c["cmd"], f"reins run {b} --self-staged -- tools/run.sh /data/x")

    def test_unregistered_heavy_job_is_seen(self):
        import subprocess, sys, time
        board.JOB_SECS, board._JOBS = 0, (0.0, [])
        p = subprocess.Popen([sys.executable, "-c", "import time\nt=time.time()\nwhile time.time()-t<20: pass"])
        try:
            time.sleep(2.5)
            jobs = board.machine_jobs(self.con)
            self.assertIn(p.pid, [j["pid"] for j in jobs])
        finally:
            p.kill(); board.JOB_SECS = 120

    def test_abandon_stale_and_refuse_fresh(self):
        old = self.batch("experiment", "running", idle_h=80)
        fresh = self.batch("experiment", "running", idle_h=1)
        os.environ["REINS_SESSION"] = "someone-else"
        with self.assertRaises(ReinsError):
            batches.abandon(self.con, fresh, "not needed any more")                     # not idle long enough
        batches.abandon(self.con, old, "superseded by a later run")
        self.assertEqual(batches.get(self.con, old)["status"], "closed")
        self.assertEqual([s["batch_id"] for s in batches.stale(self.con, 24)], [])


if __name__ == "__main__":
    unittest.main()
