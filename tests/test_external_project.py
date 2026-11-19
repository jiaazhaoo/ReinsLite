"""A project's settings kept outside its repo (adapters/<name>/reins.toml, 2026-11-19): the managed code carries no
reins file, reins finds the settings by the repo's path, from the main checkout, a candidate worktree or a release
tree; the adapter wins over a reins.toml still inside the repo."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reins import projects, runner

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


class External(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-ext-")).resolve()
        self.code = self.tmp / "code"
        self.repo = self.code / "biz"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        self.wt = self.code / "biz-wt-mod-x-1"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/x", str(self.wt))
        self.ad = self.tmp / "adapters"
        (self.ad / "biz").mkdir(parents=True)
        (self.ad / "biz/reins.toml").write_text(
            f'project = "biz"\nrepo = "{self.repo}"\n[run]\nprograms = ["go.sh"]\n'
            '[run.env]\nBIZ_RUN = "{batch}"\nBIZ_HOOK = "python3 {adapter}/hook.py"\nBIZ_V = "{stage_versions}"\n')
        (self.ad / "biz/review.toml").write_text('version = 1\n')
        self.env = {k: os.environ.get(k) for k in ("REINS_ADAPTERS", "REINS_CODE_ROOT")}
        os.environ["REINS_ADAPTERS"] = str(self.ad)
        os.environ["REINS_CODE_ROOT"] = str(self.code)
        projects._EXT.clear(); projects._CACHE.clear()

    def tearDown(self):
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        projects._EXT.clear(); projects._CACHE.clear()

    def test_found_by_repo_path_from_main_and_worktree(self):
        self.assertEqual(projects.find_root(self.repo), self.repo)
        self.assertEqual(projects.find_root(self.wt), self.repo)
        (self.wt / "sub").mkdir()
        self.assertEqual(projects.find_root(self.wt / "sub"), self.repo)
        self.assertEqual(projects.load(self.repo)["project"], "biz")
        self.assertEqual(projects.config_dir(self.repo), self.ad / "biz")
        self.assertEqual(projects.side_file(self.repo, "review.toml"), self.ad / "biz/review.toml")
        self.assertIn((self.repo, projects.load(self.repo)), projects.all_())

    def test_adapter_wins_over_reins_toml_in_repo(self):
        (self.repo / "reins.toml").write_text('project = "old"\n')
        projects._CACHE.clear()
        self.assertEqual(projects.load(self.repo)["project"], "biz")
        self.assertEqual([c["project"] for _, c in projects.all_()], ["biz"])

    def test_in_repo_settings_still_work_without_adapter(self):
        other = self.code / "other"; other.mkdir()
        git(other, "init", "-q")
        (other / "reins.toml").write_text('project = "other"\n')
        self.assertEqual(projects.find_root(other), other)
        self.assertEqual(projects.load(other)["project"], "other")
        self.assertFalse(projects.managed(self.tmp))

    def test_guard_sees_the_repo_as_managed(self):
        sys.path.insert(0, str(HOOKS))
        import guard
        self.assertTrue(guard.managed_repo(str(self.wt)))
        self.assertTrue(guard.managed_repo(str(self.repo / "a.py")))
        self.assertFalse(guard.managed_repo(str(self.tmp)))

    def test_run_env_from_project_settings(self):
        import sqlite3
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE batch_stage (batch_id, stage, module_version)")
        con.execute("INSERT INTO batch_stage VALUES ('b-1', 'georef', 'georef-x-1')")
        b = {"batch_id": "b-1", "project": "biz", "type": "experiment", "work_dir": "/w/b-1", "release_name": None}
        env = runner.project_env(con, b, "__self__")
        self.assertEqual(env["BIZ_RUN"], "b-1")
        self.assertEqual(env["BIZ_HOOK"], f"python3 {self.ad / 'biz'}/hook.py")
        self.assertEqual(json.loads(env["BIZ_V"]), {"georef": "georef-x-1"})
        self.assertEqual(runner.project_env(con, {**b, "project": "nobody"}, "s"), {})


class E2EGateWrapper(unittest.TestCase):
    GATE = Path(__file__).resolve().parents[1] / "adapters/e2e/gate.py"

    def run_gate(self, body, **env):
        d = Path(tempfile.mkdtemp(prefix="reins-gw-"))
        s = d / "g.py"; s.write_text(body)
        out = d / "out.json"
        e = {**os.environ, "REINS_GATE_OUT": str(out), "REINS_BATCH": "b", "E2E_RUN_ID": "b", **env}
        r = subprocess.run([sys.executable, str(self.GATE), sys.executable, str(s)], env=e, capture_output=True, text=True)
        return r, out

    def test_new_style_gets_out_and_no_run_variables(self):
        r, out = self.run_gate('import os, sys, json\nassert "--out" in sys.argv\n'
                               'assert "REINS_BATCH" not in os.environ and "E2E_RUN_ID" not in os.environ\n'
                               'open(sys.argv[sys.argv.index("--out") + 1], "w").write(json.dumps({"missed_error": 0}))\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(out.read_text()), {"missed_error": 0})

    def test_old_style_reads_the_variable_itself(self):
        r, out = self.run_gate('import os\nopen(os.environ["REINS_GATE_OUT"], "w").write("{}")\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text(), "{}")


class E2ERunCtl(unittest.TestCase):
    CTL = Path(__file__).resolve().parents[1] / "adapters/e2e/runctl.py"

    def test_open_translates_kind_and_prints_the_id(self):
        d = Path(tempfile.mkdtemp(prefix="reins-rc-"))
        fake = d / "reins"
        fake.write_text(f'#!/bin/bash\necho "$@" >> {d}/calls\necho "opened town-wp1-intake-20261119-3 (work dir ...)"\n')
        fake.chmod(0o755)
        r = subprocess.run([sys.executable, str(self.CTL), "open", "--project", "intake", "--scope", "town-wp1",
                            "--kind", "intake", "--stage", "receive"], env={**os.environ, "REINS_BIN": str(fake)},
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "town-wp1-intake-20261119-3", r.stderr)
        self.assertIn("batch open --project intake --scope town-wp1 --type intake --stage receive", (d / "calls").read_text())
        k = subprocess.run([sys.executable, str(self.CTL), "key", "b-1", "build_map"], capture_output=True, text=True)
        self.assertEqual(k.stdout.strip(), "b-1:build_map")


if __name__ == "__main__":
    unittest.main()
