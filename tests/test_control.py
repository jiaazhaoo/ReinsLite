"""C5, C7, C8, C9, C13: spend ledger and gateway, runner, watchdog, dev lifecycle, gate, decisions, rules."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reins import batches, decisions, dev, gate, modules, rules, runner, spend, watchdog
from reins.store import ReinsError, connect

REPO_ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ctl-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "sess-A"
        os.environ["PYTHONPATH"] = str(REPO_ROOT)
        self.con = connect(self.tmp)
        modules.add(self.con, "judge", "e2e-plan-extract", "model judges of a polygon")
        self.cases = self.tmp / "cases.txt"
        self.cases.write_text("101\n102\n103\n")

    def open(self, specs, type_="experiment", cap=0.0):
        return batches.open_(self.con, project="e2e-plan-extract", council="sheffield", wp="wp3", type_=type_,
                             purpose="test", case_file=self.cases, stages=[batches.parse_stage_spec(s) for s in specs],
                             spend_cap=cap)


class Spend(Base):
    def test_reserve_settle_caps(self):
        # incident: $0.5 cap overshot to $0.559 by 6 threads counting after the fact
        b = self.open(["judge:paid:cap=1.0"], cap=1.0)
        batches.stage_start(self.con, b, "judge")
        r1 = spend.reserve(self.con, b, "judge", 0.6)
        with self.assertRaises(spend.CapReached):                  # 0.6 held + 0.6 > 1.0 even though nothing settled
            spend.reserve(self.con, b, "judge", 0.6)
        spend.settle(self.con, r1, provider="openrouter", model="m", amount=0.3, priced="provider", tokens_in=10,
                     tokens_out=5, cache_hit=False, request_sha="x", http_status=200)
        r2 = spend.reserve(self.con, b, "judge", 0.6)             # 0.3 settled + 0.6 fits
        spend.release(self.con, r2)
        self.assertAlmostEqual(batches.spent(self.con, b), 0.3)
        self.assertEqual(spend.estimate(self.con, b, "judge", "m"), 0.3)

    def test_zero_cap_refused(self):
        b = self.open(["judge:paid"])
        batches.approve_spend(self.con, b, 1.0)
        batches.stage_start(self.con, b, "judge")
        batches.approve_spend(self.con, b, 0.0)
        with self.assertRaises(spend.CapReached):
            spend.reserve(self.con, b, "judge", 0.01)


class FakeUpstream(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        FakeUpstream.calls += 1
        n = int(self.headers["Content-Length"]); body = json.loads(self.rfile.read(n))
        assert self.headers["Authorization"] == "Bearer REALKEY", self.headers["Authorization"]
        assert body.get("usage") in ({"include": True}, None)
        out = json.dumps({"choices": [{"message": {"content": "{\"verdict\": \"correct\"}"}}],
                          "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.02}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


class Gateway(Base):
    def setUp(self):
        super().setUp()
        from reins import gateway
        self.gw = gateway
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        gateway.UPSTREAM = f"http://127.0.0.1:{self.up.server_port}"
        gateway.PROVIDERS["openrouter"] = gateway.UPSTREAM
        gateway.Handler.key = "REALKEY"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/api/v1/chat/completions"
        FakeUpstream.calls = 0

    def tearDown(self):
        self.srv.shutdown(); self.up.shutdown()

    def post(self, token, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_auth_cache_cap_pause(self):
        b = self.open(["judge:paid"], cap=0.05)
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        code, d = self.post("nope", body)
        self.assertEqual(code, 401)
        code, d = self.post(f"{b}:judge", body)
        self.assertEqual(code, 409)                                 # batch not running yet
        batches.stage_start(self.con, b, "judge")
        code, d = self.post(f"{b}:judge", body)
        self.assertEqual((code, d["choices"][0]["message"]["content"]), (200, '{"verdict": "correct"}'))
        code, d = self.post(f"{b}:judge", body)                     # identical request: cache, no upstream call
        self.assertEqual((code, FakeUpstream.calls), (200, 1))
        self.assertAlmostEqual(batches.spent(self.con, b), 0.02)
        code, d = self.post(f"{b}:judge", {**body, "messages": [{"role": "user", "content": "two"}]})
        self.assertEqual(code, 200)                                 # 0.02 + est 0.02 fits in 0.05
        code, d = self.post(f"{b}:judge", {**body, "messages": [{"role": "user", "content": "three"}]})
        self.assertEqual(code, 402)                                 # 0.04 + 0.02 > 0.05: refused, batch paused
        self.assertIn("reins batch approve", d["error"]["message"])
        self.assertEqual(batches.get(self.con, b)["status"], "paused")
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key LIKE 'cap:%'").fetchone())
        code, d = self.post(f"{b}:judge", {**body, "stream": True})
        self.assertEqual(code, 409)                                 # paused now; and streaming would be 400


class Runner(Base):
    def wait(self, pred, secs=20):
        t0 = time.time()
        while time.time() - t0 < secs:
            if pred():
                return True
            time.sleep(0.3)
        return False

    def test_run_marks_and_ends_stage(self):
        b = self.open(["ocr"])
        cmd = ["python3", "-m", "reins", "batch", "mark", b, "ocr", "--file", str(self.tmp / "marks.tsv")]
        (self.tmp / "marks.tsv").write_text("oachargeid\tstatus\treason\n101\tdone\t\n102\tdone\t\n103\tskipped\tno scans\n")
        res = runner.start(self.con, b, "ocr", cmd, self.tmp)
        self.assertTrue(self.wait(lambda: batches.get(self.con, b)["status"] == "done"), Path(res["log"]).read_text())
        self.assertEqual(self.con.execute("SELECT state, exit_code FROM process").fetchone()[:], ("exited", 0))

    def test_failure_pauses_and_notifies_after_retries(self):
        b = self.open(["ocr"])
        res = runner.start(self.con, b, "ocr", ["sh", "-c", "exit 3"], self.tmp, retries=0)
        self.assertTrue(self.wait(lambda: batches.get(self.con, b)["status"] == "paused"))
        self.assertIn("exited 3", batches.get(self.con, b)["status_reason"])
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key LIKE 'failed:%'").fetchone())

    def test_pause_resume_stop(self):
        b = self.open(["ocr"])
        runner.start(self.con, b, "ocr", ["sh", "-c", "sleep 30"], self.tmp, retries=3)
        self.assertTrue(self.wait(lambda: runner.live_processes(self.con, b)))
        pgid = runner.live_processes(self.con, b)[0]["pgid"]
        runner.ctl(self.con, "pause", b)
        self.assertEqual(batches.get(self.con, b)["status"], "paused")
        self.assertIn("T", Path(f"/proc/{pgid}/status").read_text().split("State:")[1][:4])
        runner.ctl(self.con, "resume", b)
        self.assertEqual(batches.get(self.con, b)["status"], "running")
        runner.ctl(self.con, "stop", b)
        self.assertTrue(self.wait(lambda: self.con.execute("SELECT state FROM process").fetchone()[0] == "stopped"))
        time.sleep(1)                                               # the supervisor must not retry a stopped run
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM process").fetchone()[0], 1)

    def test_fork_cannot_control(self):
        b = self.open(["ocr"])
        os.environ["REINS_SESSION"] = "sess-B"
        with self.assertRaises(ReinsError):
            runner.ctl(self.con, "pause", b)


class Watchdog(Base):
    def test_stall_and_lost(self):
        (self.tmp / "config.toml").write_text("stall_minutes = 0\n")
        b = self.open(["ocr"])
        batches.stage_start(self.con, b, "ocr")
        self.con.execute("INSERT INTO process (batch_id, stage, pgid, supervisor_pid, cmd, cwd, log_path, state, started)"
                         " VALUES (?, 'ocr', 999999, 999998, 'x', '/', '/dev/null', 'running', '2026-01-01T00:00:00')", (b,))
        found = watchdog.check_batches(self.con, {**{"stall_minutes": 0, "gateway_port": 1}})
        self.assertTrue(any("lost" in f for f in found) and any("stalled" in f for f in found), found)
        self.assertEqual(self.con.execute("SELECT state FROM process").fetchone()[0], "lost")


class Dev(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True)
        run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
        (self.repo / "reins.toml").write_text(
            'project = "e2e-plan-extract"\n[dev]\ngate = "python3 gate.py"\n[modules.judge]\nabout = "judges"\nfiles = ["judge/*"]\n')
        (self.repo / "gate.py").write_text(
            "import json, os\n"
            "json.dump({'benchmark': 'bench-t-v1', 'tiers': 'judges', 'stages_covered': 'judges', 'missed_error': int(open('miss').read()),"
            " 'review_load': 90, 'base_missed_error': 0, 'base_review_load': 96}, open(os.environ['REINS_GATE_OUT'], 'w'))\n")
        (self.repo / "miss").write_text("0")
        (self.repo / "judge").mkdir(); (self.repo / "judge" / "a.py").write_text("x = 1\n")
        run("add", "-A"); run("commit", "-q", "-m", "init")

    def test_start_finish_release(self):
        b = self.open(["judge"], type_="experiment")
        r = dev.start(self.con, self.repo, "judge", "veto", "gemini doubt is never overruled", from_batch=b, cases=["101", "102"])
        wt = Path(r["worktree"])
        self.assertTrue(wt.is_dir() and r["version"].startswith("judge-veto-"))
        self.assertEqual(json.loads(modules.get(self.con, r["version"])["pins"])["pilot_cases"], ["101", "102"])
        with self.assertRaises(ReinsError):                        # pilot cases must belong to the batch
            dev.start(self.con, self.repo, "judge", "x", "a test version that does x", from_batch=b, cases=["999"])
        (wt / "judge" / "a.py").write_text("x = 2\n")
        subprocess.run(["git", "-C", str(wt), "commit", "-qam", "change"], check=True)
        # red gate: not merged
        (wt / "miss").write_text("1"); subprocess.run(["git", "-C", str(wt), "commit", "-qam", "miss"], check=True)
        with self.assertRaises(ReinsError) as e:
            dev.finish(self.con, r["version"])
        self.assertIn("gate red", str(e.exception))
        (wt / "miss").write_text("0"); subprocess.run(["git", "-C", str(wt), "commit", "-qam", "fix"], check=True)
        out = dev.finish(self.con, r["version"])
        self.assertEqual(modules.get(self.con, r["version"])["status"], "released")
        self.assertIn("gate green", out["evidence"])
        self.assertFalse(wt.exists())
        self.assertEqual((self.repo / "judge" / "a.py").read_text(), "x = 2\n")
        rel = dev.release(self.con, self.repo, "first")
        self.assertTrue(rel["name"].startswith("rel-e2e-plan-extract-") and Path(rel["worktree"]).is_dir())
        self.assertEqual(rel["versions"], [r["version"]])
        self.assertFalse(os.access(Path(rel["worktree"]) / "judge" / "a.py", os.W_OK))

    def test_other_sessions_worktree_is_refused(self):
        r = dev.start(self.con, self.repo, "judge", "a", "a test version that does a")
        os.environ["REINS_SESSION"] = "sess-B"
        with self.assertRaises(ReinsError):
            dev.finish(self.con, r["version"])


class Small(Base):
    def test_gate_record(self):
        v = modules.new(self.con, "judge", "x", "a test version that does t", self.tmp)
        g = gate.record(self.con, v, benchmark="bench-t-v1", tiers="judges", stages_covered="judges", missed_error=1,
                        review_load=50, base_missed_error=0, base_review_load=96)
        self.assertEqual(g["status"], "red")                        # fewer reviews never excuses one more missed error
        with self.assertRaises(ReinsError):
            gate.record(self.con, v, benchmark="eval359", tiers="x", stages_covered="x", missed_error=0, review_load=0,
                        base_missed_error=0, base_review_load=0)

    def test_decisions(self):
        a = decisions.add(self.con, "sol-pro has zero missed errors", "one run, window input", "assistant")
        b = decisions.add(self.con, "sol-pro misses 162257 with whole-plan input", "bench-t-v1 run 2", "user", supersedes=a)
        act = decisions.list_(self.con)
        self.assertEqual([d["id"] for d in act], [b])
        self.assertEqual(decisions.list_(self.con, all_=True)[0]["superseded_by"], b)

    def test_rules_diff(self):
        old = self.tmp / "old.csv"; new = self.tmp / "new.csv"
        old.write_text("oachargeid,lane\n1,auto_accept\n2,auto_accept\n3,review\n")
        new.write_text("oachargeid,lane\n1,auto_accept\n2,review\n3,review\n4,review\n")
        d = rules.diff(old, new)
        self.assertEqual([(m["from"], m["to"], m["n"]) for m in d["moves"]],
                         [("(absent)", "review", 1), ("auto_accept", "review", 1)])
        (self.tmp / "rules.toml").write_text('[[rule]]\nid="a"\nlane="review"\nwhen="no plan_image"\nevidence="e"\ndecided="2026-10-05"\nby="user"\n')
        self.assertIn("no plan_image", rules.describe(rules.load(self.tmp / "rules.toml")))
        (self.tmp / "bad.toml").write_text('[[rule]]\nid="a"\nlane="review"\n')
        with self.assertRaises(ReinsError):
            rules.load(self.tmp / "bad.toml")


if __name__ == "__main__":
    unittest.main()


class Drift(Gateway):
    def test_drift_batch_bypasses_cache(self):
        b = self.open(["judge:paid"], cap=1.0)
        batches.stage_start(self.con, b, "judge")
        body = {"model": "m", "messages": [{"role": "user", "content": "same"}]}
        self.post(f"{b}:judge", body); self.post(f"{b}:judge", body)
        self.assertEqual(FakeUpstream.calls, 1)                   # second is a cache hit
        d = batches.open_(self.con, project="e2e-plan-extract", council="sheffield", wp="wp3", type_="drift",
                          purpose="weekly drift", case_file=self.cases, stages=[batches.parse_stage_spec("judge:paid")], spend_cap=1.0)
        batches.stage_start(self.con, d, "judge")
        self.post(f"{d}:judge", body); self.post(f"{d}:judge", body)
        self.assertEqual(FakeUpstream.calls, 3)                   # drift asks the live model every time


class Providers(Gateway):
    def test_batch_token_uses_running_stage_and_deepseek_route(self):
        b = self.open(["ocr", "judge:paid"], cap=1.0)
        batches.stage_start(self.con, b, "ocr")
        batches.mark(self.con, b, "ocr", [(c, "done", None) for c in ("101", "102", "103")])
        batches.stage_end(self.con, b, "ocr")
        batches.stage_start(self.con, b, "judge")
        code, d = self.post(b, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(code, 200)
        self.assertEqual(self.con.execute("SELECT stage FROM spend").fetchone()[0], "judge")
        self.gw.PROVIDERS["deepseek"] = self.gw.UPSTREAM                # fake upstream answers both
        self.gw.KEYS["deepseek"] = "REALKEY"
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/p/deepseek/v1/chat/completions",
                                     data=json.dumps({"model": "deepseek-chat", "messages": []}).encode(),
                                     headers={"Authorization": f"Bearer {b}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.con.execute("SELECT provider FROM spend ORDER BY id DESC LIMIT 1").fetchone()[0], "deepseek")


class SelfStaged(Runner):
    def test_command_drives_stages(self):
        b = self.open(["prepare", "work"])
        R = str(REPO_ROOT / "bin" / "reins")
        script = (f"{R} batch stage-start {b} prepare && {R} batch mark {b} prepare --file {self.tmp}/m.tsv && "
                  f"{R} batch stage-end {b} prepare && {R} batch stage-start {b} work && "
                  f"{R} batch mark {b} work --file {self.tmp}/m.tsv && {R} batch stage-end {b} work")
        (self.tmp / "m.tsv").write_text("oachargeid\tstatus\treason\n101\tdone\t\n102\tdone\t\n103\tskipped\tno scans\n")
        res = runner.start(self.con, b, runner.SELF, ["sh", "-c", script], self.tmp)
        self.assertTrue(self.wait(lambda: batches.get(self.con, b)["status"] == "done"), Path(res["log"]).read_text())
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key=?", (f"done:{b}",)).fetchone())
