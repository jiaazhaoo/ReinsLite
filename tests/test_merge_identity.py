"""A project chooses who signs reins' merge commits and tags, keeps the gate's evidence out of its merge messages and
chosen words out of its history (2026-11-19: e2e is business code whose history must not name the framework)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from reins import dev
from reins.store import ReinsError, connect


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


class MergeIdentity(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-mi-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "sess-A"
        self.con = connect(self.tmp / "home")
        self.repo = self.tmp / "proj"; self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "config", "user.email", "t@t"); git(self.repo, "config", "user.name", "t")
        (self.repo / "reins.toml").write_text(
            'project = "p"\n[dev]\ngate = "echo evidence-from-the-framework"\n'
            'merge_author = "biz-bot <biz-bot@example.org>"\nmerge_message = "short"\nhistory_forbid = ["framework"]\n'
            '[modules.m]\nabout = "a module"\nfiles = ["a.txt"]\n')
        (self.repo / "a.txt").write_text("one\n")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_merge_signed_by_the_project_and_short(self):
        r = dev.start(self.con, self.repo, "m", "two", "the file says two")
        wt = Path(r["worktree"])
        (wt / "a.txt").write_text("two\n")
        git(wt, "commit", "-qam", "two")
        dev.finish(self.con, r["version"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an <%ae>", "main"), "biz-bot <biz-bot@example.org>")
        body = git(self.repo, "log", "-1", "--format=%B", "main")
        self.assertEqual(body, f"Merge feat/{r['version']}: the file says two")
        self.assertNotIn("framework", body)

    def test_forbidden_word_refused_at_start(self):
        with self.assertRaises(ReinsError):
            dev.start(self.con, self.repo, "m", "framework_fix", "x")
        with self.assertRaises(ReinsError):
            dev.start(self.con, self.repo, "m", "fix", "talks about the Framework")

    def test_default_identity_is_unchanged(self):
        self.assertEqual(dev.ident({}), dev.IDENT)
        self.assertEqual(dev.ident({"dev": {"merge_author": "A B <a@b>"}}), ["-c", "user.name=A B", "-c", "user.email=a@b"])
        self.assertEqual(dev.forbidden_in_history({}, "anything"), [])


if __name__ == "__main__":
    unittest.main()
