"""C17: prompt / model / workflow versions; content-addressed; pinned by module versions and workflows; several in use."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from reins import artifacts, batches, dev, modules
from reins.store import ReinsError, connect


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


class Artifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-art-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "sess-A"
        self.con = connect(self.tmp / "home")
        self.repo = self.tmp / "proj"; self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "config", "user.email", "t@t"); git(self.repo, "config", "user.name", "t")
        (self.repo / "reins.toml").write_text(
            'project = "p"\n[dev]\ngate = "true"\n'
            '[prompts.judge]\nfile = "judge/j.py"\nsymbol = "PROMPT"\n'
            '[models.judge_primary]\nprovider = "openrouter"\nid = "google/gemini-3.8-flash"\nparams = {temperature = 0}\n'
            '[modules.judge]\nabout = "model judges of a polygon"\nfiles = ["judge/*"]\nprompts = ["judge"]\nmodels = ["judge_primary"]\n'
            '[workflows.qa]\nabout = "the qa flow"\nstages = [{name = "prepare"}, {name = "check", module = "judge", prompts = ["judge"], models = ["judge_primary"], paid = true}]\n')
        (self.repo / "judge").mkdir()
        (self.repo / "judge" / "j.py").write_text('MODEL = "x"\nPROMPT = """You check a polygon.\nAnswer JSON."""\n')
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "init")
        self.cfg = dev.project_cfg(self.repo)

    def test_content_addressed_versions(self):
        a = artifacts.scan(self.con, self.repo, self.cfg)
        self.assertEqual(a["prompt:judge"], "prompt-judge-v1")
        self.assertEqual(artifacts.scan(self.con, self.repo, self.cfg)["prompt:judge"], "prompt-judge-v1")   # same text
        (self.repo / "judge" / "j.py").write_text('MODEL = "x"\nPROMPT = """You check a polygon carefully.\nAnswer JSON."""\n')
        self.assertEqual(artifacts.scan(self.con, self.repo, self.cfg)["prompt:judge"], "prompt-judge-v2")
        self.assertIn("-You check a polygon.", artifacts.diff(self.con, "prompt-judge-v1", "prompt-judge-v2"))
        self.assertEqual(a["model:judge_primary"], "model-judge_primary-v1")
        with self.assertRaises(ReinsError):
            artifacts.diff(self.con, "prompt-judge-v1", "model-judge_primary-v1")

    def test_fstring_prompt_is_refused(self):
        (self.repo / "judge" / "j.py").write_text('X = 1\nPROMPT = f"""You check {X}."""\n')
        with self.assertRaises(ReinsError):
            artifacts.scan(self.con, self.repo, self.cfg)

    def test_finish_pins_and_workflow_freezes_the_set(self):
        r = dev.start(self.con, self.repo, "judge", "stricter", "a stricter judge prompt")
        wt = Path(r["worktree"])
        (wt / "judge" / "j.py").write_text('MODEL = "x"\nPROMPT = """You check a polygon strictly.\nAnswer JSON."""\n')
        git(wt, "commit", "-qam", "stricter prompt")
        dev.finish(self.con, r["version"])
        pins = json.loads(modules.get(self.con, r["version"])["pins"])
        self.assertEqual(pins["artifacts"], ["prompt-judge-v1", "model-judge_primary-v1"])   # first text seen = v1
        self.assertEqual(artifacts.get(self.con, "prompt-judge-v1")["status"], "active")
        w = artifacts.freeze_workflow(self.con, self.repo, self.cfg, "qa")
        self.assertEqual(w["name"], "workflow-qa-v1")
        st = artifacts.workflow_stages(self.con, "workflow-qa-v1")
        self.assertEqual((st[1]["module_version"], st[1]["prompts"], st[1]["paid"]), (r["version"], ["prompt-judge-v1"], True))
        self.assertFalse(artifacts.freeze_workflow(self.con, self.repo, self.cfg, "qa")["new"])      # unchanged set
        # a batch opened from the workflow carries the whole set in its provenance
        cases = self.tmp / "c.txt"; cases.write_text("1\n2\n")
        self.con.execute("INSERT INTO release VALUES ('rel-p-20261007-1','p','abc',?, '', '[]', '2026', NULL)", (str(self.tmp),))
        b = batches.open_(self.con, project="p", council="x", wp="wp1", type_="production", purpose="from workflow",
                          case_file=cases, stages=[], release_name="rel-p-20261007-1", workflow="workflow-qa-v1")
        prov = batches.provenance(self.con, b)
        self.assertEqual(prov["workflow"], "workflow-qa-v1")
        self.assertEqual(prov["module_versions"], {"prepare": None, "check": r["version"]})
        self.assertEqual(prov["artifacts"][r["version"]], ["prompt-judge-v1", "model-judge_primary-v1"])
        self.assertEqual(self.con.execute("SELECT paid FROM batch_stage WHERE batch_id=? AND stage='check'", (b,)).fetchone()[0], 1)
        self.assertIn(r["version"], artifacts.pinned_in(self.con, "prompt-judge-v1")["module_versions"])


if __name__ == "__main__":
    unittest.main()
