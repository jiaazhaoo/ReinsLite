"""C10 resource admission and C8 kill detection (incident 2026-10-13: two batches in parallel, ~11 GB judges each,
the machine's watchdog killed children three times and the pipeline went on on a missing input for ~2 h)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from reins import batches, modules, resources, runner
from reins.store import ReinsError, connect

REPO_ROOT = Path(__file__).resolve().parents[1]
R = str(REPO_ROOT / "bin" / "reins")


def rule(name, mem=0.0, gpu=0.0, tags=(), stage=None, match=None):
    return {"name": name, "stage": stage, "match": match, "mem_gb": mem, "gpu_gb": gpu, "tags": sorted(tags)}


class Base(unittest.TestCase):
    CONFIG = ("[resources]\nmem_total_gb = 16\nmax_fraction = 1.0\nmem_reserved_gb = 1\ngpu_total_gb = 8\n"
              "poll_s = 0.2\n")
    PROJECT = ""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-res-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "sess-A"
        self._code_root = os.environ.get("REINS_CODE_ROOT")
        os.environ["REINS_CODE_ROOT"] = tempfile.mkdtemp(prefix="reins-res-code-")    # no real project's reins.toml
        os.environ["PYTHONPATH"] = str(REPO_ROOT)
        (self.tmp / "config.toml").write_text(self.CONFIG)
        (self.tmp / "reins.toml").write_text('project = "proj"\n' + self.PROJECT)
        self.con = connect(self.tmp)
        modules.add(self.con, "work", "proj", "a module")
        self.cases = self.tmp / "cases.txt"
        self.cases.write_text("101\n102\n")
        self.cap = resources.budget()

    def tearDown(self):
        if self._code_root is None:
            os.environ.pop("REINS_CODE_ROOT", None)
        else:
            os.environ["REINS_CODE_ROOT"] = self._code_root

    def open(self, specs=("work",)):
        return batches.open_(self.con, project="proj", scope="res-test", type_="experiment", purpose="test",
                             case_file=self.cases, stages=[batches.parse_stage_spec(s) for s in specs])

    def running(self, specs=("work",)):
        b = self.open(specs)
        self.con.execute("UPDATE batch SET status='running' WHERE batch_id=?", (b,))
        return b

    def wait(self, pred, secs=20):
        t0 = time.time()
        while time.time() - t0 < secs:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def claims(self, rule_name=None):
        q = "SELECT * FROM resource_claim" + (" WHERE rule=?" if rule_name else "") + " ORDER BY id"
        return self.con.execute(q, (rule_name,) if rule_name else ()).fetchall()


class Admission(Base):
    def test_budget_from_config(self):
        self.assertEqual((self.cap["mem"], self.cap["gpu"]), (15.0, 8.0))

    def test_fits(self):
        g = [{"id": 1, "batch_id": "b", "rule": "judge", "mem_gb": 11, "gpu_gb": 0, "tags": "[]"}]
        self.assertFalse(resources.fits(g, rule("judge", mem=11), {"mem": 15, "gpu": None})[0])
        self.assertTrue(resources.fits(g, rule("small", mem=4), {"mem": 15, "gpu": None})[0])
        self.assertTrue(resources.fits([], rule("huge", mem=40), {"mem": 15, "gpu": None})[0])   # alone
        gt = [{"id": 2, "batch_id": "b", "rule": "ocr", "mem_gb": 0, "gpu_gb": 1, "tags": '["gpu"]'}]
        ok, why = resources.fits(gt, rule("vlm", gpu=1, tags=["gpu"]), {"mem": 15, "gpu": 8})
        self.assertFalse(ok); self.assertIn("gpu held by claim #2", why)
        self.assertTrue(resources.fits(gt, rule("judge", mem=11), {"mem": 15, "gpu": 8})[0])  # different tag/dimension

    def test_first_come_first_served(self):
        a, b, c = self.running(), self.running(), self.running()
        j1 = resources.request(self.con, a, "work", rule("judge", mem=11))
        j2 = resources.request(self.con, b, "work", rule("judge", mem=11))
        small = resources.request(self.con, c, "work", rule("small", mem=2))
        self.assertTrue(resources.try_grant(self.con, j1, self.cap)[0])
        ok, why = resources.try_grant(self.con, j2, self.cap)
        self.assertFalse(ok); self.assertIn("mem 11+11", why)
        ok, why = resources.try_grant(self.con, small, self.cap)       # would fit, but j2 asked first: no overtaking
        self.assertFalse(ok); self.assertIn(f"behind claim #{j2}", why)
        resources.release(self.con, j1)
        self.assertTrue(resources.try_grant(self.con, j2, self.cap)[0])
        self.assertTrue(resources.try_grant(self.con, small, self.cap)[0])   # 11 + 2 <= 15

    def test_paused_batch_does_not_block_the_queue(self):
        a, b, c = self.running(), self.running(), self.running()
        j1 = resources.request(self.con, a, "work", rule("judge", mem=11))
        resources.try_grant(self.con, j1, self.cap)
        resources.request(self.con, b, "work", rule("judge", mem=11))     # waits ...
        batches.set_status(self.con, b, "paused", "by hand", system=True)  # ... and its batch is paused
        small = resources.request(self.con, c, "work", rule("small", mem=2))
        self.assertTrue(resources.try_grant(self.con, small, self.cap)[0])

    def test_claim_of_a_dead_process_is_released(self):
        a = self.running()
        p = subprocess.Popen(["true"]); p.wait()
        cid = resources.request(self.con, a, "work", rule("judge", mem=11), pid=p.pid, owner_pid=p.pid)
        self.con.execute("UPDATE resource_claim SET state='granted' WHERE id=?", (cid,))
        self.assertEqual(resources.cleanup(self.con), 1)
        self.assertEqual(self.claims()[0]["state"], "released")

    def test_config_check(self):
        probs = resources.check_rules({"resources": {"a": {"stage": "x", "match": "y", "mem_gb": 1},
                                                     "b": {"match": "("}, "c": {"stage": "s", "mem_gb": 2}}})
        self.assertEqual(len([p for p in probs if "[resources.a]" in p]), 1)
        self.assertTrue(any("[resources.b] match does not compile" in p for p in probs))
        self.assertTrue(any("[resources.b] declares nothing" in p for p in probs))
        self.assertFalse(any("[resources.c]" in p for p in probs))


class StageAdmission(Base):
    PROJECT = '[resources.big]\nstage = "work"\nmem_gb = 10\n'

    def test_second_batch_waits_for_the_first(self):
        a, b = self.open(), self.open()
        runner.start(self.con, a, "work", ["sh", "-c", "sleep 2"], self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: runner.live_processes(self.con, a)))
        res = runner.start(self.con, b, "work", ["sh", "-c", "sleep 0.5"], self.tmp, retries=0)
        self.assertIn("waiting_claim", res)                            # b's supervisor waits: nothing started
        self.assertFalse(self.con.execute("SELECT 1 FROM process WHERE batch_id=?", (b,)).fetchone())
        self.assertTrue(self.wait(lambda: self.con.execute("SELECT 1 FROM process WHERE batch_id=?", (b,)).fetchone()))
        pa = self.con.execute("SELECT * FROM process WHERE batch_id=?", (a,)).fetchone()
        pb = self.con.execute("SELECT * FROM process WHERE batch_id=?", (b,)).fetchone()
        self.assertEqual(pa["state"], "exited")                        # b started only after a's process ended
        self.assertGreaterEqual(pb["started"], pa["ended"])
        self.assertEqual(runner.wait(self.con, b, poll_s=0.2)["timed_out"], False)
        self.assertEqual([c["state"] for c in self.claims("big")], ["released", "released"])
        self.assertTrue(self.con.execute("SELECT 1 FROM batch_event WHERE batch_id=? AND event='resource_wait'", (b,)).fetchone())

    def test_self_staged_stage_start_blocks(self):
        a, b = self.open(), self.open()
        (self.tmp / "m.tsv").write_text("case_id\tstatus\treason\n101\tdone\t\n102\tdone\t\n")
        script = lambda bid, pause: (f"{R} batch stage-start {bid} work && sleep {pause} && "
                                     f"{R} batch mark {bid} work --file {self.tmp}/m.tsv && {R} batch stage-end {bid} work")
        runner.start(self.con, a, runner.SELF, ["sh", "-c", script(a, 2)], self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: batches._stage(self.con, a, "work")["status"] == "running"))
        runner.start(self.con, b, runner.SELF, ["sh", "-c", script(b, 0)], self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: any(c["state"] == "waiting" and c["batch_id"] == b for c in self.claims())))
        self.assertEqual(batches._stage(self.con, b, "work")["status"], "planned")   # still held at stage-start
        self.assertTrue(self.wait(lambda: batches.get(self.con, b)["status"] == "done", 30))
        self.assertEqual(batches.get(self.con, a)["status"], "done")
        ends = {r["batch_id"]: r for r in self.con.execute("SELECT * FROM batch_stage")}
        self.assertGreaterEqual(ends[b]["started"], ends[a]["ended"])


class MatchAdmission(Base):
    PROJECT = '[resources.judge]\nmatch = "judge_marker"\nmem_gb = 11\n'

    def test_parallel_judges_never_overlap(self):
        a = self.open()
        step = "python3 -c 'import time; time.sleep(1.5)  # judge_marker'"
        (self.tmp / "run.sh").write_text(f"{step} &\n{step} &\nwait\n")
        runner.start(self.con, a, "work", ["sh", str(self.tmp / "run.sh")], self.tmp, retries=0)
        most, held_seen = 0, False
        t0 = time.time()
        while time.time() - t0 < 15:
            rows = self.claims("judge")
            most = max(most, sum(r["state"] == "granted" for r in rows))
            for r in rows:
                if r["state"] == "waiting" and r["pid"]:
                    try:
                        held_seen |= "T" in Path(f"/proc/{r['pid']}/status").read_text().split("State:")[1][:4]
                    except OSError:
                        pass
            if len(rows) == 2 and all(r["state"] == "released" for r in rows):
                break
            time.sleep(0.05)
        rows = self.claims("judge")
        self.assertEqual(len(rows), 2, [dict(r) for r in rows])        # one claim per judge process, not per thread
        self.assertEqual(most, 1)
        self.assertTrue(held_seen)
        self.assertTrue(all(r["state"] == "released" for r in rows))
        self.assertGreaterEqual(rows[1]["granted"], rows[0]["released"][:19])


class Kill(Base):
    def test_killed_command_pauses_without_retry(self):
        a = self.open()
        runner.start(self.con, a, "work", ["sh", "-c", "kill -9 $$"], self.tmp, retries=3)
        self.assertTrue(self.wait(lambda: batches.get(self.con, a)["status"] == "paused"))
        b = batches.get(self.con, a)
        self.assertTrue(b["status_reason"].startswith(resources.KILLED), b["status_reason"])
        self.assertTrue(self.wait(lambda: self.con.execute("SELECT 1 FROM notification WHERE key LIKE 'killed:%'").fetchone()))
        self.assertTrue(self.con.execute("SELECT 1 FROM batch_event WHERE event='killed'").fetchone())
        time.sleep(1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM process").fetchone()[0], 1)   # no retry
        with self.assertRaises(ReinsError) as e:
            runner.ctl(self.con, "resume", a)
        self.assertIn("--after-kill", str(e.exception))
        runner.ctl(self.con, "resume", a, after_kill=True)
        self.assertEqual(batches.get(self.con, a)["status"], "running")

    def test_kill_line_pauses_before_the_next_step(self):
        log = self.tmp / "kill.log"; log.write_text("")
        with (self.tmp / "config.toml").open("a") as f:
            f.write(f'[kill_watch]\ncmd = "tail -n 0 -F {log}"\n')
        a = self.open()
        (self.tmp / "run.sh").write_text(f"python3 -c 'import time; time.sleep(60)' &\necho $! > {self.tmp}/pid\nwait\n"
                                         f"touch {self.tmp}/next_step\n")
        runner.start(self.con, a, "work", ["sh", str(self.tmp / "run.sh")], self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: (self.tmp / "pid").exists() and (self.tmp / "pid").read_text().strip()))
        pid = int((self.tmp / "pid").read_text())
        time.sleep(1)                                                  # the monitor has seen it in the tree
        with log.open("a") as f:                                       # the machine's watchdog logs, then kills
            f.write(f"resource-watchdog: memory over threshold; killing pid={pid} name='python' rss=10769MiB\n")
        self.assertTrue(self.wait(lambda: batches.get(self.con, a)["status"] == "paused"))
        os.kill(pid, 9)
        time.sleep(1)
        self.assertFalse((self.tmp / "next_step").exists())            # the command did not go on
        ev = self.con.execute("SELECT detail FROM batch_event WHERE event='killed'").fetchone()[0]
        self.assertIn(f"pid {pid}", ev); self.assertIn("killing pid=", ev)
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key=?", (f"killed:{a}:{pid}",)).fetchone())
        runner.ctl(self.con, "stop", a)
        self.assertTrue(self.wait(lambda: not runner.live_processes(self.con, a)))
        self.assertFalse((self.tmp / "next_step").exists())

    def test_unrelated_kill_line_is_ignored(self):
        log = self.tmp / "kill.log"; log.write_text("")
        with (self.tmp / "config.toml").open("a") as f:
            f.write(f'[kill_watch]\ncmd = "tail -n 0 -F {log}"\n')
        a = self.open()
        (self.tmp / "m.tsv").write_text("case_id\tstatus\treason\n101\tdone\t\n102\tdone\t\n")
        runner.start(self.con, a, "work", ["sh", "-c", f"sleep 1.5; {R} batch mark {a} work --file {self.tmp}/m.tsv"],
                     self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: runner.live_processes(self.con, a)))
        log.write_text("resource-watchdog: memory over threshold; killing pid=1 name='init'\n")
        self.assertTrue(self.wait(lambda: batches.get(self.con, a)["status"] == "done"))


class Detached(Base):
    def test_run_says_detached_and_wait_blocks(self):
        a = self.open()
        (self.tmp / "m.tsv").write_text("case_id\tstatus\treason\n101\tdone\t\n102\tdone\t\n")
        r = subprocess.run([R, "run", "--cwd", str(self.tmp), a, "work", "--", "sh", "-c",
                            f"sleep 1; {R} batch mark {a} work --file {self.tmp}/m.tsv"],
                           capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("detached: live process #", r.stdout); self.assertIn(f"reins wait {a}", r.stdout)
        w = subprocess.run([R, "wait", a], capture_output=True, text=True, env=os.environ, timeout=60)
        self.assertEqual((w.returncode, w.stdout.strip()), (0, f"{a}: done"), w.stderr)
        self.assertFalse(runner.live_processes(self.con, a))


if __name__ == "__main__":
    unittest.main()
