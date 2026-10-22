"""`reins run` parses the way the docs write it (2026-10-22: `reins run B --self-staged -- CMD` failed, REMAINDER took
the option; sessions found other spellings by trial)."""
from __future__ import annotations

import unittest

from reins import cli


class RunArgv(unittest.TestCase):
    def parse(self, argv):
        pre, cmd = cli._split_run(argv)
        a = cli._run_options(pre)
        return a.batch, a.stage, a.self_staged, a.retries, cmd

    def test_spellings(self):
        self.assertEqual(self.parse(["run", "B", "--self-staged", "--", "tools/run.sh", "E"]), ("B", None, True, None, ["tools/run.sh", "E"]))
        self.assertEqual(self.parse(["run", "--self-staged", "B", "--", "x"]), ("B", None, True, None, ["x"]))
        self.assertEqual(self.parse(["run", "B", "georef", "--retries", "0", "--", "x", "--self-staged"]),
                         ("B", "georef", False, 0, ["x", "--self-staged"]))     # the command's own flags are its own


if __name__ == "__main__":
    unittest.main()
