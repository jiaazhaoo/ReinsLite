"""Hourly remote check (2026-11-03: a lost GitHub login stopped every push silently)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import dev, projects
from reins.store import connect


def sh(*a, cwd=None):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


class RemoteLogin(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("REINS_HOME")
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-rl-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        (self.tmp / "home").mkdir()
        self.con = connect(self.tmp / "home")
        self.remote = self.tmp / "remote.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.repo = self.tmp / "repo"
        sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            sh("git", "config", k, v, cwd=self.repo)
        (self.repo / "a").write_text("1")
        sh("git", "add", "a", cwd=self.repo); sh("git", "commit", "-qm", "one", cwd=self.repo)
        sh("git", "remote", "add", "origin", str(self.remote), cwd=self.repo)
        sh("git", "push", "-q", "origin", "main", cwd=self.repo)
        (self.repo / "a").write_text("2")
        sh("git", "commit", "-qam", "two", cwd=self.repo)                     # merged while the login was gone
        self.cfg = {"project": "p", "dev": {"push": "origin"}}

    def tearDown(self):
        if self._env is None:
            os.environ.pop("REINS_HOME", None)
        else:
            os.environ["REINS_HOME"] = self._env

    def test_behind_is_pushed_at_once(self):
        with mock.patch.object(projects, "all_", return_value=[(self.repo, self.cfg)]):
            out = dev.remote_login(self.con)
        self.assertTrue(any("behind" in x and "ok" in x for x in out), out)
        theirs = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertEqual(theirs.strip(), dev.git(self.repo, "rev-parse", "main"))
        with mock.patch.object(projects, "all_", return_value=[(self.repo, self.cfg)]):
            self.assertEqual(dev.remote_login(self.con), [])                 # at most hourly

    def test_lost_login_is_one_notice(self):
        sh("git", "remote", "set-url", "origin", str(self.tmp / "gone.git"), cwd=self.repo)
        with mock.patch.object(projects, "all_", return_value=[(self.repo, self.cfg)]):
            out = dev.remote_login(self.con)
        self.assertTrue(out and "FAILED" in out[0], out)
        n = self.con.execute("SELECT COUNT(*) FROM notification WHERE key='remote-login:p'").fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
