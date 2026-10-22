"""Services restart onto merged code. 2026-10-22: the gateway ran six hours of code that did not know the batch type
`intake` and refused every intake batch's paid call with 401; `reins dev finish` merges but restarts nothing."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import watchdog
from reins.store import connect


class CodeReload(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("REINS_HOME")
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-cr-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        self.con = connect(self.tmp)
        self.cfg = {"gateway_port": 1}
        self.done = []

    def tearDown(self):
        if self._env is None:
            os.environ.pop("REINS_HOME", None)
        else:
            os.environ["REINS_HOME"] = self._env

    def run_(self, starts, inflight):
        return watchdog.check_code_fresh(self.con, self.cfg, started=lambda u: starts[u], code_time=lambda: 100.0,
                                         inflight=lambda: inflight, restart=lambda u: self.done.append(u) or True)

    def test_old_services_restart_gateway_waits_for_calls(self):
        starts = {"reins-board": 50.0, "reins-gateway": 50.0, "reins-watchdog": 50.0}
        out = self.run_(starts, inflight=2)
        self.assertEqual(self.done, ["reins-board", "reins-watchdog"])          # gateway waits: 2 paid calls in flight
        self.assertTrue(any("waiting for 2" in o for o in out))
        self.done.clear()
        self.run_(starts, inflight=0)
        self.assertIn("reins-gateway", self.done)
        self.assertEqual(self.done[-1], "reins-watchdog")                       # the watchdog restarts itself last

    def test_fresh_services_untouched(self):
        self.run_({"reins-board": 150.0, "reins-gateway": 150.0, "reins-watchdog": None}, inflight=0)
        self.assertEqual(self.done, [])


if __name__ == "__main__":
    unittest.main()
