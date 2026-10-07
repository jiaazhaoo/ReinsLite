"""C15: sessions seen through the Claude Code hook; development and runs derived from them; no session detail on the board."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reins import board, sessions
from reins.store import connect

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "session_hook.py"


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-sess-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        self.con = connect(self.tmp / "home")
        self.repo = self.tmp / "proj"; self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "config", "user.email", "t@t"); git(self.repo, "config", "user.name", "t")
        (self.repo / "reins.toml").write_text('project = "p"\n[modules.georef]\nabout = "place drawings on the map"\nfiles = ["georef/*"]\n'
                                              '[modules.judge]\nabout = "model judges of a polygon"\nfiles = ["judge/*"]\n')
        (self.repo / "georef").mkdir(); (self.repo / "georef" / "a.py").write_text("x=1\n")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "init")
        self.wt = self.tmp / "proj-wt-x"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/x", str(self.wt), "main")
        self.transcript = self.tmp / "t.jsonl"
        self.transcript.write_text("".join(json.dumps({"type": "user", "message": {"content": f"m{i}"}}) + "\n" for i in range(5)))

    def hook(self, sid, ev, **kw):
        data = {"session_id": sid, "hook_event_name": ev, "cwd": str(kw.pop("cwd", self.repo)), **kw}
        env = {**os.environ, "REINS_HOME": str(self.tmp / "home")}
        env.pop("REINS_SESSION", None)
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=env)

    def test_session_lifecycle_development_and_conflicts(self):
        r = self.hook("aaaa1111", "SessionStart", transcript_path=str(self.transcript), source="startup")
        self.assertIn("You are session aaaa1111", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        # a fork: same history prefix
        fork_t = self.tmp / "fork.jsonl"; fork_t.write_text(self.transcript.read_text() + json.dumps({"type": "user", "message": {"content": "new"}}) + "\n")
        r = self.hook("bbbb2222", "SessionStart", transcript_path=str(fork_t), source="resume")
        self.assertIn("FORKED from session aaaa1111", r.stdout)
        # A edits georef in its worktree; B edits georef in main -> same module + edits_main
        self.hook("aaaa1111", "PostToolUse", tool_name="Edit", tool_input={"file_path": str(self.wt / "georef" / "a.py")}, cwd=self.wt)
        self.hook("bbbb2222", "PostToolUse", tool_name="Write", tool_input={"file_path": str(self.repo / "georef" / "b.py")})
        s = {r["id"]: r for r in self.con.execute("SELECT * FROM session")}
        self.assertEqual((s["aaaa1111"]["role"], s["aaaa1111"]["module"]), ("develop", "georef"))
        self.assertEqual(s["bbbb2222"]["parent"], "aaaa1111")
        kinds = {c["kind"] for c in sessions.conflicts(self.con)}
        self.assertTrue({"same_module", "edits_main"} <= kinds, kinds)
        # a run command and a blocked detached start
        self.hook("cccc3333", "PreToolUse", tool_name="Bash", tool_input={"command": "reins run sheffield-wp3-pilot-20261007-1 --self-staged -- tools/run_local_qa.sh /data/x"})
        r = self.hook("cccc3333", "PreToolUse", tool_name="Bash", tool_input={"command": "nohup tools/run_local_qa.sh /data/x &"})
        self.assertEqual(r.returncode, 2)
        ev = [e["kind"] for e in sessions.timeline(self.con, "cccc3333")]
        self.assertEqual(ev[:2], ["blocked", "run_start"])
        self.assertEqual(self.con.execute("SELECT role FROM session WHERE id='cccc3333'").fetchone()[0], "run")
        # read-only commands are not recorded
        self.hook("cccc3333", "PreToolUse", tool_name="Bash", tool_input={"command": "ls -la /data"})
        self.assertEqual(len(sessions.timeline(self.con, "cccc3333")), 2)
        # the board: development items and runs, never a session id or a path
        st = board.state(self.con)
        dev = [d for d in st["developing"] if d["module"] == "georef"]
        self.assertEqual(len(dev), 1)
        self.assertIn("另一项开发也在改 georef", " ".join(dev[0]["conflicts"]) + " 2 个会话同时在做")
        blob = json.dumps({"developing": st["developing"], "running": st["running"], "u": st["unregistered_runs"]}, ensure_ascii=False)
        for secret in ("aaaa1111", "bbbb2222", "cccc3333", str(self.wt)):
            self.assertNotIn(secret, blob)
        self.hook("aaaa1111", "SessionEnd", reason="exit")
        self.assertEqual(self.con.execute("SELECT status FROM session WHERE id='aaaa1111'").fetchone()[0], "ended")

    def test_provenance_index(self):
        from reins import batches, modules
        os.environ["REINS_SESSION"] = "dddd4444"
        cases = self.tmp / "c.txt"; cases.write_text("1\n2\n")
        b = batches.open_(self.con, project="p", council="x", wp="wp1", type_="experiment", purpose="index test",
                          case_file=cases, stages=[batches.parse_stage_spec("s")])
        self.hook("eeee5555", "PreToolUse", tool_name="Bash", tool_input={"command": f"reins ctl pause {b}"})
        idx = {d["session"]: d for d in sessions.index_for_batch(self.con, b)}
        self.assertEqual(set(idx), {"dddd4444", "eeee5555"})
        self.assertIn("run_stop", idx["eeee5555"]["actions"])
        modules.add(self.con, "georef", "p", "place drawings on the map")
        v = modules.new(self.con, "georef", "x", "plan road names place the drawing", self.repo)
        self.assertEqual([d["session"] for d in sessions.index_for_module(self.con, v)], ["dddd4444"])
        del os.environ["REINS_SESSION"]

    def test_classify(self):
        c = sessions.classify
        self.assertEqual(c("reins ctl pause sheffield-wp3-production-20261007-1"), "run_stop")
        self.assertEqual(c("kill -STOP 4124997"), "run_stop")
        self.assertEqual(c("reins batch open --type experiment --council x"), "experiment")
        self.assertEqual(c("python3 tools/dev/dev.py finish x"), "merge")
        self.assertEqual(c("git commit -m x"), "git")
        self.assertEqual(c("cat /data/x/y.json"), "read")

    def test_hook_never_breaks_a_session(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True,
                           env={**os.environ, "REINS_HOME": str(self.tmp / "home")})
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
