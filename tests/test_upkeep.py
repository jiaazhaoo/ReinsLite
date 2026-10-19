"""The registry stays true over months: a daily checked backup, running batches whose process is gone become paused
(once, with the reason), leases nobody can need go, and resuming nothing does not make a batch look busy.

Regression for 2026-10-19: 37 batches marked running with no process alive, 2,575 stall/overtime notices in 3 days
nobody received, 68 leases held by ended or idle sessions, and the last registry backup 11 days old."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, runner, upkeep
from reins.store import connect, now, tx


def ago(**kw) -> str:
    return (dt.datetime.now() - dt.timedelta(**kw)).isoformat(timespec="seconds")


class Upkeep(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-up-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        self.con = connect(self.tmp / "home")
        (self.tmp / "cases.txt").write_text("101\n102\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def batch(self, created_h=48):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="upkeep",
                          case_file=self.tmp / "cases.txt", stages=[{"stage": "s", "module_version": None, "paid": 0,
                                                                     "spend_cap": None, "time_limit_s": None}])
        batches.stage_start(self.con, b, "s")
        with tx(self.con):
            self.con.execute("UPDATE batch SET created=? WHERE batch_id=?", (ago(hours=created_h), b))
        return b

    def proc(self, b, state, started, ended=None, code=None):
        with tx(self.con):
            self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, state, exit_code,"
                             " started, ended) VALUES (?, '*', 999999, 999999, 'run.sh', '/', '/x', ?, ?, ?, ?)",
                             (b, state, code, started, ended))

    def test_gone_processes_pause_the_batch_once(self):
        lost = self.batch(); self.proc(lost, "running", ago(hours=5))
        ended = self.batch(); self.proc(ended, "exited", ago(hours=5), ago(hours=3), 0)
        fresh = self.batch(); self.proc(fresh, "exited", ago(minutes=40), ago(minutes=10), 0)      # ended 10 min ago: wait
        unrun_new = self.batch(created_h=2)                                                          # never run by reins
        unrun_old = self.batch(created_h=30)
        out = upkeep.reconcile(self.con)
        st = {b: batches.get(self.con, b) for b in (lost, ended, fresh, unrun_new, unrun_old)}
        self.assertEqual(st[lost]["status"], "paused"); self.assertTrue(st[lost]["status_reason"].startswith("process gone"))
        self.assertIn("ended normally (exit 0)", st[ended]["status_reason"])
        self.assertEqual((st[fresh]["status"], st[unrun_new]["status"]), ("running", "running"))
        self.assertEqual(st[unrun_old]["status"], "paused")
        self.assertEqual(len(out), 3)
        self.assertEqual(upkeep.reconcile(self.con), [])                                             # once
        lost_proc = self.con.execute("SELECT state FROM process WHERE batch_id=?", (lost,)).fetchone()[0]
        self.assertEqual(lost_proc, "lost")

    def test_resume_without_a_process_stays_paused(self):
        b = self.batch(); self.proc(b, "exited", ago(hours=5), ago(hours=3), 0)
        upkeep.reconcile(self.con)
        msg = runner.ctl(self.con, "resume", b, system=True)
        self.assertIn("stays paused", msg)
        self.assertEqual(batches.get(self.con, b)["status"], "paused")

    def test_backup_daily_checked_and_kept(self):
        r = upkeep.backup(self.con)
        self.assertTrue(r and r[0].startswith("backup "))
        self.assertEqual(upkeep.backup(self.con), [])                    # less than a day old
        f = upkeep.last_backup()
        old = f.with_name("reins-20200101-0000.db"); old.write_bytes(f.read_bytes())
        os.utime(old, (0, 0))
        upkeep.backup(self.con, force=True)
        self.assertFalse(old.exists())                                   # past keep_days
        import sqlite3
        self.assertEqual(sqlite3.connect(upkeep.last_backup()).execute("SELECT COUNT(*) FROM batch").fetchone()[0], 0)

    def test_leases_nobody_can_need(self):
        b = self.batch()
        with tx(self.con):
            self.con.execute("UPDATE batch SET status='done' WHERE batch_id=?", (b,))
            self.con.execute("INSERT OR REPLACE INTO lease VALUES (?, 'gone-session', 'x', ?)", (f"batch:{b}", now()))
            self.con.execute("INSERT INTO lease VALUES ('worktree:/nonexistent/wt', 'gone-session', 'x', ?)", (now(),))
            self.con.execute("INSERT INTO lease VALUES (?, 'owner', 'x', ?)", (f"worktree:{self.tmp}", now()))
        out = upkeep.release_leases(self.con)
        self.assertEqual(out, ["released 2 lease(s) nobody can need"])
        left = [r[0] for r in self.con.execute("SELECT resource FROM lease")]
        self.assertIn(f"worktree:{self.tmp}", left)


if __name__ == "__main__":
    unittest.main()
