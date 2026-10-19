"""The board as a person opens it: every page and every data endpoint answers, the front-end scripts parse, and the
production numbers agree with the ledger. Board changes were checked by hand in a browser until now; a page that
does not open now fails the gate instead."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from reins import batches, board
from reins.store import connect, tx


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION", "REINS_CODE_ROOT")}
        cls.tmp = Path(tempfile.mkdtemp(prefix="reins-smoke-"))
        os.environ["REINS_HOME"] = str(cls.tmp / "home")
        os.environ["REINS_SESSION"] = "owner"
        os.environ["REINS_CODE_ROOT"] = str(cls.tmp / "code")
        (cls.tmp / "code").mkdir()
        con = connect(cls.tmp / "home")
        (cls.tmp / "cases.txt").write_text("101\n102\n103\n")
        st = [{"stage": "s", "module_version": None, "paid": 0, "spend_cap": None, "time_limit_s": None}]
        for i, (typ, status) in enumerate((("production", "done"), ("rework", "paused"), ("experiment", "running"))):
            b = batches.open_(con, project="p", scope=f"town-wp{i}", type_="experiment", purpose=f"smoke {i}",
                              case_file=cls.tmp / "cases.txt", stages=st)
            with tx(con):
                con.execute("UPDATE batch SET type=?, status=?, status_reason=? WHERE batch_id=?",
                            (typ, status, "disk /data at 91%" if status == "paused" else None, b))
                con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES "
                            "('2026-10-10T12:00:00', ?, 's', 'x', 'm', 1.25, 'provider')", (b,))
        con.close()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def test_every_page_and_endpoint_answers(self):
        for path, kind in (("/", "text/html"), ("/production", "text/html"), ("/app.js", "javascript"),
                           ("/production.js", "javascript"), ("/app.css", "text/css"), ("/production.css", "text/css"),
                           ("/api/state", "json"), ("/api/production", "json")):
            code, ctype, body = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(kind, ctype, path)
            self.assertTrue(body, path)
        html = self.get("/production")[2].decode()
        self.assertIn("production.js", html)

    def test_scripts_parse(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        for js in board.UI.glob("*.js"):
            r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{js.name}: {r.stderr}")

    def test_numbers_agree_with_the_ledger(self):
        v = json.loads(self.get("/api/production")[2])
        self.assertEqual(v["totals"]["n_cases"], 6)                        # production + rework, not the experiment
        self.assertEqual(v["totals"]["spent"], 2.5)
        self.assertEqual(v["dev"]["spent"], 1.25)                          # the experiment is development
        self.assertEqual(round(v["totals"]["spent"] + v["dev"]["spent"], 2), v["totals"]["ai_all"])
        s = json.loads(self.get("/api/state")[2])
        self.assertIn("running", s)
        self.assertTrue(any(r["batch_id"].startswith("town-wp1") for r in s["running"]))   # the paused rework needs a person


if __name__ == "__main__":
    unittest.main()
