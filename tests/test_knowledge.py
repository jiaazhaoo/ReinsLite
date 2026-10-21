"""C17: what one session learns about a module or a running service is where the next one looks.

Regression for 2026-10-21: :8773 already served three councils from a merged view, a session read only the server's
flags and said it could not; a second session offered to fix what a third was fixing. Neither fact was anywhere."""
from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path

from reins import issues, knowledge
from reins.store import ReinsError, connect, now, tx


class Knowledge(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-kn-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "me"
        self.con = connect(self.tmp / "home")
        knowledge.ensure(self.con)
        with tx(self.con):
            self.con.execute("INSERT INTO module VALUES ('review', 'p', 'human review platform', ?)", (now(),))
            self.con.execute("INSERT INTO module_version (version, module, suffix, day, seq, status, about, session, created)"
                             " VALUES ('review-wfs_multi-20261021-1', 'review', 'wfs_multi', '20261021', 1, 'candidate',"
                             " 'snap basemap per council', 'other-session', ?)", (now(),))
            self.con.execute("INSERT INTO session (id, started, last_seen, status, purpose) VALUES "
                             "('other-session', ?, ?, 'active', 'fix basemap snapping on the review page')", (now(), now()))
        self.sock = socket.socket(); self.sock.bind(("0.0.0.0", 0)); self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def tearDown(self):
        self.sock.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_service_found_adopted_and_briefed(self):
        un = knowledge.discover(self.con)["unregistered"]
        self.assertIn(self.port, [u["port"] for u in un])                       # listening on 0.0.0.0, nobody registered it
        knowledge.adopt(self.con, self.port, "qa-review-study3", "merged review of three councils for Maggie, Jia, Yishan",
                        data=["/data/x/study3/view"], built_by="/data/x/study3/build.py", module="review")
        self.assertNotIn(self.port, [u["port"] for u in knowledge.discover(self.con)["unregistered"]])
        knowledge.note_add(self.con, "qa-review-study3", "serves several councils at once: a merged view and an empty case prefix")
        iid = issues.open_(self.con, None, [], "Ctrl-snap has OS lines for Mansfield only", service="qa-review-study3")
        b = knowledge.brief(self.con, "review")
        for want in ("IN PROGRESS review-wfs_multi-20261021-1", "fix basemap snapping", f":{self.port}", "merged view",
                     f"OPEN PROBLEM #{iid}", "built by /data/x/study3/build.py"):
            self.assertIn(want, b)
        self.assertIn("merged view", knowledge.brief(self.con, "qa-review-study3"))
        self.assertEqual(knowledge.mentioned(self.con, f"put the errors on {self.port} for Maggie"), ["qa-review-study3"])
        self.assertIn("review", knowledge.mentioned(self.con, "the review platform needs two councils"))
        self.assertFalse(knowledge.briefed(self.con, "me", "review"))
        self.assertTrue(knowledge.briefed(self.con, "me", "review"))           # once per session

    def test_issue_on_module_needs_a_target_and_restart_is_seen(self):
        with self.assertRaises(ReinsError):
            issues.open_(self.con, None, [], "something is wrong somewhere")
        i = issues.open_(self.con, None, [], "page header overlaps on narrow screens", module="review")
        self.assertEqual(issues.get(self.con, i)["module"], "review")
        knowledge.adopt(self.con, self.port, "svc-a", "a test service listening for this test")
        with tx(self.con):
            self.con.execute("UPDATE service SET pid=1 WHERE name='svc-a'")
        knowledge.discover(self.con)
        ev = [r[0] for r in self.con.execute("SELECT event FROM service_event WHERE service='svc-a'")]
        self.assertIn("restarted", ev)
        self.sock.close()
        knowledge.discover(self.con)
        self.assertEqual(knowledge.get(self.con, "svc-a")["status"], "down")

    def test_notes_need_a_known_subject(self):
        with self.assertRaises(ReinsError):
            knowledge.note_add(self.con, "nonexistent", "a fact about something nobody registered")
        n = knowledge.note_add(self.con, "review", "the history db only shows past verdicts, it loads no cases")
        knowledge.note_retire(self.con, n, "replaced by a merged-view loader")
        self.assertEqual(knowledge.notes(self.con, "review"), [])


if __name__ == "__main__":
    unittest.main()
