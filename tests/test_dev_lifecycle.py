"""Candidates and issues reach an end without a person (2026-10-22: 6 candidates idle up to 13 days, 4 fixed issues
never verified, issues left on a closed batch and a retired service)."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from reins import issues, knowledge, lifecycle
from reins.store import connect, now, tx


def ago(**kw):
    return (dt.datetime.now() - dt.timedelta(**kw)).isoformat(timespec="seconds")


class DevLifecycle(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-dl-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        os.environ["REINS_SESSION"] = "me"
        self.con = connect(self.tmp / "home")
        knowledge.ensure(self.con)
        with tx(self.con):
            self.con.execute("INSERT INTO module VALUES ('georef', 'p', 'place plans', ?)", (now(),))
            for sid, st in (("gone", "ended"), ("live", "active")):
                self.con.execute("INSERT INTO session (id, started, last_seen, status) VALUES (?,?,?,?)", (sid, ago(days=20), now(), st))
            for n, (v, sess, days) in enumerate((("georef-old-20261009-1", "gone", 13), ("georef-busy-20261009-2", "live", 13),
                                                 ("georef-new-20261021-1", "gone", 1))):
                self.con.execute("INSERT INTO module_version (version, module, suffix, day, seq, status, about, session, created)"
                                 " VALUES (?, 'georef', ?, '20261009', ?, 'candidate', 'x', ?, ?)",
                                 (v, v.split("-")[1], n + 1, sess, ago(days=days)))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_idle_candidate_abandoned_and_its_issue_reopened(self):
        iid = issues.open_(self.con, None, [], "plans placed one street off", module="georef")
        issues.take(self.con, iid, "georef-old-20261009-1")
        out = lifecycle.tend_candidates(self.con)
        self.assertEqual(out, ["georef-old-20261009-1: abandoned (13 days idle)"])   # active session's and recent one's stay
        st = {r[0]: r[1] for r in self.con.execute("SELECT version, status FROM module_version")}
        self.assertEqual((st["georef-busy-20261009-2"], st["georef-new-20261021-1"]), ("candidate", "candidate"))
        self.assertEqual(issues.get(self.con, iid)["status"], "open")

    def test_issues_on_retired_service_and_fixed_never_rerun(self):
        with tx(self.con):
            self.con.execute("INSERT INTO service (name, port, about, status, registered, code_ref, data) VALUES "
                             "('old-page', 8800, 'an old review page', 'retired', ?, 'x', '[]')", (now(),))
        a = issues.open_(self.con, None, [], "snapping has no lines outside one council", service="old-page")
        b = issues.open_(self.con, None, [], "header overlaps the map on narrow screens", module="georef")
        with tx(self.con):
            self.con.execute("UPDATE issue SET status='fixed', version='georef-x-20261001-1' WHERE id=?", (b,))
            self.con.execute("INSERT INTO issue_event (at, issue_id, event, detail) VALUES (?,?, 'fixed', 'released')", (ago(days=20), b))
        out = lifecycle.tend_issues(self.con)
        self.assertEqual(sorted(out), sorted([f"issue #{a}: closed (service retired)", f"issue #{b}: closed (fixed, never re-run)"]))


if __name__ == "__main__":
    unittest.main()
