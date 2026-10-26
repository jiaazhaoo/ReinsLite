"""[dev] post_release (2026-10-26): a command run in the new release tree after the push, e.g. publishing the
release's assets for other machines; a failure is an action notice, never an undone release."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import dev
from reins.store import connect


class PostRelease(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("REINS_HOME")
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-postrel-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        self.con = connect()
        self.wt = self.tmp / "tree"; self.wt.mkdir()

    def tearDown(self):
        os.environ.pop("REINS_HOME", None) if self._env is None else os.environ.__setitem__("REINS_HOME", self._env)

    def test_runs_in_the_tree_with_the_release_named(self):
        out = self.tmp / "seen"
        r = dev.post_release(self.con, {"dev": {"post_release": f'echo "$REINS_RELEASE $PWD" > {out}'}}, "qa-20261026-9", self.wt)
        self.assertTrue(r["ok"])
        self.assertEqual(out.read_text().split(), ["qa-20261026-9", str(self.wt)])

    def test_none_without_the_setting_and_a_notice_on_failure(self):
        self.assertIsNone(dev.post_release(self.con, {"dev": {}}, "x", self.wt))
        r = dev.post_release(self.con, {"dev": {"post_release": "echo boom; exit 3"}}, "qa-x", self.wt)
        self.assertFalse(r["ok"]); self.assertIn("boom", r["tail"])
        n = self.con.execute("SELECT COUNT(*) FROM notification WHERE key=?", ("post_release:qa-x",)).fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
