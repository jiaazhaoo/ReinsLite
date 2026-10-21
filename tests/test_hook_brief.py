"""The hook hands shared knowledge to the session that needs it: a prompt naming a service (by name or port) or a
module gets that subject's brief once; the first edit of a module's file gets the module's brief; a session that never
bound a purpose is known by its first prompt."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reins import knowledge
from reins.store import connect, now, tx

ROOT = Path(__file__).resolve().parents[1]


class HookBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-hb-"))
        self.env = {**os.environ, "REINS_HOME": str(self.tmp / "home"), "PYTHONPATH": str(ROOT)}
        os.environ["REINS_HOME"] = self.env["REINS_HOME"]
        self.con = connect(self.tmp / "home")
        knowledge.ensure(self.con)
        with tx(self.con):
            self.con.execute("INSERT INTO module VALUES ('review', 'p', 'human review platform', ?)", (now(),))
            self.con.execute("INSERT INTO service (name, module, port, about, status, registered, code_ref, data) VALUES "
                             "('qa-review-study3', 'review', 8773, 'merged review of three councils', 'live', ?, 'main checkout', '[]')",
                             (now(),))
        knowledge.note_add(self.con, "qa-review-study3", "one port serves several councils: a merged view, case prefix left empty")

    def hook(self, payload):
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_hook.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env, timeout=60)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def test_prompt_naming_a_port_gets_the_brief_once_and_sets_purpose(self):
        self.hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(self.tmp)})
        ctx = self.hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "把 error 都放到 8773 给 Maggie"})
        self.assertIn("merged view", ctx)
        self.assertIn("qa-review-study3", ctx)
        again = self.hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "8773 还有问题"})
        self.assertNotIn("merged view", again)                                  # once per session and subject
        purpose = connect(self.tmp / "home").execute("SELECT purpose FROM session WHERE id='s1'").fetchone()[0]
        self.assertTrue(purpose.startswith("(first prompt) 把 error"))


if __name__ == "__main__":
    unittest.main()
