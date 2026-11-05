"""Hints (2026-11-05): an expired login in a command's output, or a reply that says it fell back to a stale index,
gets the configured fix at once."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import hints

CFG = r'''
[[hints]]
name = "expired-login"
output = "session has expired|ExpiredToken"
reply = "登录已过期.{0,60}(旧|索引|缓存)"
message = "use the key that never expires; never a stale copy"
'''


class Hints(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("REINS_HOME")
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-hints-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        (self.tmp / "config.toml").write_text(CFG)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("REINS_HOME", None)
        else:
            os.environ["REINS_HOME"] = self._home

    def test_output_once_per_session(self):
        out = "aws: [ERROR]: Your session has expired. Please reauthenticate using 'aws login'."
        self.assertEqual(len(hints.for_output("s1", hints._text({"stdout": "", "stderr": out}))), 1)
        self.assertEqual(hints.for_output("s1", out), [])                     # not again within the hour
        self.assertEqual(len(hints.for_output("s2", out)), 1)                 # another session is told too
        self.assertEqual(hints.for_output("s3", "listed 43 prefixes"), [])

    def test_reply_that_reports_the_workaround(self):
        said = "线上的 S3 现在列不了，因为 AWS 登录已过期，所以用的是 8 月 21 日建的 S3 文件夹索引。"
        self.assertTrue(hints.for_reply([said]))
        self.assertEqual(hints.for_reply(["S3 列出了 43 个目录。"]), [])

    def test_bad_rule_is_skipped(self):
        (self.tmp / "config.toml").write_text('[[hints]]\nname = "x"\noutput = "("\nmessage = "m"\n')
        self.assertEqual(hints.rules(), [])


if __name__ == "__main__":
    unittest.main()
