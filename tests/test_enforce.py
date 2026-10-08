"""No way around reins in a managed repo: edits, pipeline programs, legacy tools, conflicts, upstream gate, time budget."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reins import batches, dev, modules
from reins.store import ReinsError, connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


class Enforce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-enf-"))
        self.code = self.tmp / "code"; self.code.mkdir()
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_CODE_ROOT"] = str(self.code)
        os.environ["REINS_SESSION"] = "sess-A"
        self.con = connect(self.tmp / "home")
        self.repo = self.code / "proj"; self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "config", "user.email", "t@t"); git(self.repo, "config", "user.name", "t")
        (self.repo / "reins.toml").write_text(
            'project = "proj"\n[dev]\ngate = "true"\nrelease_prefix = "qa"\nblocked_tools = ["tools/dev/dev.py"]\n'
            '[dev.upstream]\nfiles = ["up/*"]\ngate = "exit 7"\n'
            '[run]\nprograms = ["run_local_qa.sh", "boundary_lab.py"]\n'
            '[modules.georef]\nabout = "place drawings on the map"\nfiles = ["georef/*"]\n'
            '[modules.up]\nabout = "upstream stage of the pipeline"\nfiles = ["up/*"]\n')
        for d, f in (("georef", "a.py"), ("up", "u.py"), ("tools", "run_local_qa.sh")):
            (self.repo / d).mkdir(exist_ok=True); (self.repo / d / f).write_text("x=1\n")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "init")
        import guard
        importlib.reload(guard)
        guard._PROJECTS = None
        self.guard = guard

    def tearDown(self):
        os.environ.pop("REINS_CODE_ROOT", None)

    def test_edits_only_in_own_candidate_worktree(self):
        g = self.guard
        self.assertIn("main worktree", g.check_edit(str(self.repo / "georef" / "a.py")))
        wt = self.code / "proj-wt-old"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/old", str(wt), "main")
        self.assertIn("reins dev adopt", g.check_edit(str(wt / "georef" / "a.py")))       # made by a legacy tool
        self.assertNotIn("main worktree", g.check_edit(str(wt / "georef" / "a.py")))     # a worktree is not the main repo
        dev.adopt(self.con, wt, "georef", "old", "roads before geocode in the old branch")
        self.assertIsNone(g.check_edit(str(wt / "georef" / "a.py")))                      # now this session's candidate
        os.environ["REINS_SESSION"] = "sess-B"
        self.assertIn("sess-A", g.check_edit(str(wt / "georef" / "a.py")))               # another session's
        os.environ["REINS_SESSION"] = "sess-A"
        self.assertIsNone(g.check_edit(str(self.tmp / "elsewhere.txt")))                 # outside managed repos

    def test_pipeline_programs_only_through_reins(self):
        g = self.guard
        P = "run_local" + "_qa.sh"
        self.assertTrue(g.check_bash(f"bash tools/{P} /data/x"))
        self.assertTrue(g.check_bash(f"cd /data && START_STEP=14 tools/{P} /data/x"))
        self.assertTrue(g.check_bash("/env/venv/yolo/bin/python tools/boundary_lab.py local --lab x"))
        wrapper = self.tmp / "resume_from14.sh"
        wrapper.write_text(f"#!/bin/bash\ncd /x\nSTART_STEP=14 bash tools/{P} /data/x\n")
        self.assertIn("runs a pipeline program", g.check_bash(f"bash {wrapper}"))
        self.assertIsNone(g.check_bash(f"reins run B --self-staged -- tools/{P} /data/x"))
        self.assertIsNone(g.check_bash(f"cat tools/{P}"))
        self.assertTrue(g.check_bash("python3 tools/dev/dev.py start x --area a --files f --why w"))
        self.assertIsNone(g.check_bash("python3 tools/dev/dev.py list"))

    def test_start_refuses_overlap_unless_justified(self):
        dev.start(self.con, self.repo, "georef", "a", "roads before geocode")
        with self.assertRaises(ReinsError):
            dev.start(self.con, self.repo, "georef", "b", "another georef change")
        r = dev.start(self.con, self.repo, "georef", "b", "another georef change", overlap_ok="different functions, agreed order")
        self.assertIn("overlap_ok", modules.get(self.con, r["version"])["pins"])

    def test_upstream_files_need_the_upstream_gate(self):
        r = dev.start(self.con, self.repo, "up", "x", "change an upstream stage")
        wt = Path(r["worktree"]); (wt / "up" / "u.py").write_text("x=2\n")
        git(wt, "commit", "-qam", "up change")
        with self.assertRaises(ReinsError) as e:                  # the upstream gate here exits 7
            dev.finish(self.con, r["version"])
        self.assertIn("exit 7", str(e.exception))
        out = dev.finish(self.con, r["version"], skip_tier="upstream", why="GPU busy; no step command changed")
        self.assertIn("skipped", out["evidence"])

    def test_release_keeps_the_project_prefix(self):
        git(self.repo, "tag", "-a", "qa-20990101-1", "-m", "x")                          # tags made before reins count
        rel = dev.release(self.con, self.repo, "first")
        self.assertRegex(rel["name"], r"^qa-\d{8}-\d+$")
        self.assertIn("-rel-qa-", rel["worktree"])

    def test_time_budget(self):
        cases = self.tmp / "c.txt"; cases.write_text("1\n2\n")
        b = batches.open_(self.con, project="proj", scope="x-wp1", type_="experiment", purpose="time budget test",
                          case_file=cases, stages=[batches.parse_stage_spec("s")])
        batches.set_time_budget(self.con, b, 0.0001)
        batches.stage_start(self.con, b, "s")
        self.con.execute("UPDATE batch_stage SET started='2020-01-01T00:00:00' WHERE batch_id=?", (b,))
        from reins import watchdog
        found = watchdog.check_batches(self.con, {"stall_minutes": 10 ** 9, "gateway_port": 9})
        self.assertTrue(any("time budget" in f for f in found), found)


if __name__ == "__main__":
    unittest.main()
