"""The paid gateway cannot hang unseen. Regression for 2026-10-22: every handler thread waited on a lock for 53 minutes
(CPU 0, 363 threads), no paid call went through for any batch, nothing noticed, and nobody could see where it was stuck.

Now: a deep health check that a stuck gateway cannot answer, a watchdog that dumps its threads and restarts it after
three silent minutes, a cap on requests handled at once (503 instead of a thread pile-up), and no schema work per request."""
from __future__ import annotations

import faulthandler
import json
import os
import signal
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from reins import gateway, watchdog
from reins.store import connect, enable_stack_dumps


class Liveness(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("REINS_HOME")
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-gw-"))
        os.environ["REINS_HOME"] = str(self.tmp / "home")
        self.con = connect(self.tmp / "home")
        self.srv = gateway.Server(("127.0.0.1", 0), gateway.Handler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        gateway._ACTIVE = threading.BoundedSemaphore(gateway.MAX_ACTIVE)
        if self._env is None:
            os.environ.pop("REINS_HOME", None)
        else:
            os.environ["REINS_HOME"] = self._env

    def get(self, path, timeout=10):
        try:
            with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_deep_health_answers_and_sees_a_stuck_lock(self):
        code, body = self.get("/health/deep")
        self.assertEqual((code, body["ok"], body["inflight"]), (200, True, 0))
        gateway._INFLIGHT_LOCK.acquire()                     # a thread that never lets go
        try:
            code, body = self.get("/health/deep")
        finally:
            gateway._INFLIGHT_LOCK.release()
        self.assertEqual((code, body["ok"]), (503, False))

    def test_saturated_gateway_answers_503_at_once(self):
        gateway._ACTIVE = threading.BoundedSemaphore(1)
        gateway._ACTIVE.acquire()                            # the one slot is taken and never returned
        old = gateway.WAIT_S
        gateway.WAIT_S = 0.3
        try:
            code, _ = self.get("/health", timeout=5)
        finally:
            gateway.WAIT_S = old
        self.assertEqual(code, 503)

    def test_watchdog_restarts_after_three_silent_minutes(self):
        watchdog._GW_FAILS = 0
        cfg = {"gateway_port": 1}
        restarted = []

        def silent(url):
            raise TimeoutError("timed out")
        self.assertEqual(watchdog.check_gateway(self.con, cfg, probe=lambda u: {"ok": True}), [])
        for _ in range(2):
            self.assertIn("did not answer", watchdog.check_gateway(self.con, cfg, probe=silent, restart=restarted.append)[0])
        out = watchdog.check_gateway(self.con, cfg, probe=silent, restart=lambda u: restarted.append(u) or True)
        self.assertEqual((out, restarted), (["gateway hung: restarted"], ["reins-gateway"]))
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key='gateway_hung'").fetchone())

    def test_stack_dumps_and_no_schema_per_request(self):
        enable_stack_dumps()
        self.assertTrue(faulthandler.unregister(signal.SIGUSR1))      # it was registered
        c = connect(self.tmp / "home", ensure=False)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM batch").fetchone()[0], 0)
        c.close()


if __name__ == "__main__":
    unittest.main()
