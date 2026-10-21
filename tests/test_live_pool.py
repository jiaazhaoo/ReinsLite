"""C17 live: a change to a module or a service reaches the sessions working on it at their next tool call or prompt;
commands that touch a live service are recorded as they run; a session that changed something and wrote nothing is
asked once before it stops."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reins import issues, knowledge
from reins.store import connect, now, tx

ROOT = Path(__file__).resolve().parents[1]


class Live(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-live-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        self.con = connect(self.tmp / "home")
        knowledge.ensure(self.con)
        self.data = self.tmp / "study3"; self.data.mkdir()
        with tx(self.con):
            self.con.execute("INSERT INTO module VALUES ('review', 'p', 'human review platform', ?)", (now(),))
            self.con.execute("INSERT INTO service (name, module, port, unit, about, status, registered, code_ref, data) VALUES "
                             "('study3', 'review', 8773, 'qa-review-study3.service', 'merged review', 'live', ?, 'main', ?)",
                             (now(), json.dumps([str(self.data)])))
            for sid in ("A", "B"):
                self.con.execute("INSERT INTO session (id, started, last_seen, status) VALUES (?,?,?, 'active')", (sid, now(), now()))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def as_(self, sid):
        os.environ["REINS_SESSION"] = sid

    def test_change_reaches_the_follower_once(self):
        knowledge.briefed(self.con, "A", "study3")                       # A looked at the service
        self.assertEqual(knowledge.pending(self.con, "A"), [])           # the first call sets where A starts
        self.as_("B")
        knowledge.note_add(self.con, "study3", "the merged view takes a fourth council with the same build script")
        issues.open_(self.con, None, [], "snapping has no OS lines outside Mansfield", service="study3")
        got = knowledge.pending(self.con, "A")
        self.assertEqual([g["kind"] for g in got], ["fact", "problem_opened"])
        self.assertEqual(knowledge.pending(self.con, "A"), [])           # told once
        self.assertEqual(knowledge.pending(self.con, "B"), [])           # nobody hears their own changes
        self.assertIn("fourth council", knowledge.render_changes(got))

    def test_module_followers_hear_about_its_services(self):
        with tx(self.con):                                               # A edits the review module
            self.con.execute("INSERT INTO session_event (at, session_id, kind, module) VALUES (?, 'A', 'edit', 'review')", (now(),))
        knowledge.pending(self.con, "A")
        knowledge.capture_command(self.con, "B", "systemctl --user restart qa-review-study3", "/tmp")
        got = knowledge.pending(self.con, "A")
        self.assertEqual([(g["subject"], g["kind"]) for g in got], [("study3", "touched")])

    def test_capture_reads_what_runs_not_what_is_mentioned(self):
        d = str(self.data)
        cases = [("systemctl --user restart qa-review-study3.service", "/tmp", True),
                 ("python3 build_study3_errors.py", d, True),
                 (f"echo x > {d}/assign.csv", "/tmp", True),
                 (f"sqlite3 {d}/review.db 'delete from assignments'", "/tmp", True),
                 (f"cat {d}/records.json | head", "/tmp", False),
                 (f"ls {d}", "/tmp", False),
                 ("systemctl --user status qa-review-study3", "/tmp", False)]
        for cmd, cwd, want in cases:
            self.assertEqual(bool(knowledge.capture_command(self.con, "A", cmd, cwd)), want, cmd)

    def test_unrecorded_until_a_fact_is_written(self):
        knowledge.capture_command(self.con, "A", "python3 build.py", str(self.data))
        self.assertEqual(knowledge.unrecorded(self.con, "A"), ["study3"])
        self.as_("A")
        knowledge.service_event(self.con, "study3", "Maggie's list replaced by the errors of two councils")
        self.assertEqual(knowledge.unrecorded(self.con, "A"), [])

    def test_hook_stop_asks_once(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        knowledge.capture_command(self.con, "A", "python3 build.py", str(self.data))

        def stop():
            r = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_hook.py")], capture_output=True, text=True,
                               input=json.dumps({"hook_event_name": "Stop", "session_id": "A"}), env=env, timeout=60)
            return json.loads(r.stdout) if r.stdout.strip() else {}
        first = stop()
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("study3", first["reason"])
        self.assertEqual(stop(), {})                                     # asked once per subject


if __name__ == "__main__":
    unittest.main()
