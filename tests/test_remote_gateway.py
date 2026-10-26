"""A shared gateway (2026-10-26): a colleague's machine runs its own reins without provider keys; its gateway forwards
uncached calls to the machine that holds them, as a named remote client with its own caps. The shared gateway runs
here as a separate process with its own REINS_HOME; the client gateway runs in this process."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reins import batches, gateway, providers, remote, spend
from reins.store import connect

ROOT = Path(__file__).resolve().parents[1]


class Upstream(BaseHTTPRequestHandler):
    seen: list = []
    jobs: dict = {}

    def log_message(self, *a):
        pass

    def _out(self, code, d):
        b = json.dumps(d).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Upstream.seen.append((self.path, self.headers.get("Authorization") or self.headers.get("x-goog-api-key"), body))
        if self.path.endswith("/chat/completions"):
            self._out(200, {"model": body["model"], "choices": [{"message": {"content": "live"}}],
                            "usage": {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010}}); return
        Upstream.jobs["batches/j1"] = body["batch"]["input_config"]["requests"]["requests"]
        self._out(200, {"name": "batches/j1", "metadata": {"state": "BATCH_STATE_PENDING"}})

    def do_GET(self):
        if self.path.startswith("/maps/"):
            Upstream.seen.append((self.path, None, None))
            self._out(200, {"status": "OK", "results": [{"x": 1}]}); return
        reqs = Upstream.jobs.get(self.path.lstrip("/"))
        rows = [{"metadata": r["metadata"], "response": {"candidates": [{"content": {"parts": [{"text": f"b{i}"}]}, "finishReason": "STOP"}],
                                                         "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10}}}
                for i, r in enumerate(reqs or [])]
        self._out(200, {"name": "batches/j1", "done": True, "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                        "response": {"inlinedResponses": {"inlinedResponses": rows}}})


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class RemoteGateway(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-remote-"))
        self.S, self.C = self.tmp / "shared", self.tmp / "client"
        self.S.mkdir(); self.C.mkdir()
        Upstream.seen, Upstream.jobs = [], {}
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        up = f"http://127.0.0.1:{self.up.server_port}"
        # the shared machine: real keys, a price, client alice with $1 a day
        self.port = free_port()
        (self.S / "config.toml").write_text(
            f'gateway_port = {self.port}\n[providers.gemini]\nbase = "{up}/openai"\n'
            f'[providers.google_maps]\nbase = "{up}"\n[remote_clients.alice]\ndaily_cap = 1.0\n')
        (self.S / "secrets" / "clients").mkdir(parents=True)
        (self.S / "secrets" / "gemini.key").write_text("GEKEY\n")
        (self.S / "secrets" / "google.key").write_text("MAPSKEY\n")
        (self.S / "secrets" / "clients" / "alice.token").write_text("s3cret\n")
        sc = connect(self.S); spend.set_price(sc, "gemini-3.8-flash", 0.75, 3.75, "test"); sc.close()
        env = {**os.environ, "REINS_HOME": str(self.S), "PYTHONPATH": str(ROOT)}
        self.proc = subprocess.Popen([sys.executable, "-m", "reins", "gateway", "serve"], env=env, cwd=str(self.tmp),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1); break
            except Exception:                                                    # noqa: BLE001
                time.sleep(0.1)
        # the client machine: no keys, [remote] + its token
        os.environ["REINS_HOME"] = str(self.C)
        os.environ["REINS_SESSION"] = "owner"
        (self.C / "config.toml").write_text(f'[remote]\ngateway = "http://127.0.0.1:{self.port}"\nclient = "alice"\n')
        (self.C / "secrets").mkdir()
        (self.C / "secrets" / "remote.token").write_text("s3cret\n")
        self.con = connect(self.C)
        gateway.KEYS.clear()
        self.srv = gateway.Server(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        (self.C / "cases.txt").write_text("101\n")
        self.b = self.open_batch("experiment")

    def open_batch(self, type_):
        b = batches.open_(self.con, project="p", scope="x-wp1", type_=type_, purpose="remote",
                          case_file=self.C / "cases.txt", stages=[batches.parse_stage_spec("judge:paid")], spend_cap=5.0)
        batches.stage_start(self.con, b, "judge")
        return b

    def tearDown(self):
        self.srv.shutdown(); self.up.shutdown()
        self.proc.terminate(); self.proc.wait(10)
        self.proc.stderr.close()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, body=None, batch=None, method=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {batch or self.b}:judge", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    @staticmethod
    def chat(text):
        return {"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": text}], "temperature": 0}

    def shared(self, q, args=()):
        c = connect(self.S, ensure=False)
        try:
            return c.execute(q, args).fetchall()
        finally:
            c.close()

    def test_client_spec_points_at_the_shared_gateway_without_keys(self):
        g = providers.get("gemini")
        self.assertEqual((g["via"], g["base"]), ("remote", f"http://127.0.0.1:{self.port}/p/gemini/v1"))
        self.assertEqual(providers.key(g), "rc|alice|s3cret")
        self.assertIn("gemini", [p["name"] for p in providers.in_use()])

    def test_chat_booked_on_both_sides_shared_cache_and_drift(self):
        code, d = self.call("/p/gemini/v1/chat/completions", self.chat("a"))
        self.assertEqual((code, d["choices"][0]["message"]["content"]), (200, "live"))
        self.assertEqual(Upstream.seen[-1][1], "Bearer GEKEY")                    # the real key only on the shared machine
        want = (1000 * 0.75 + 10 * 3.75) / 1e6
        (amt, priced, mine), = self.con.execute("SELECT amount, priced, batch_id FROM spend WHERE cache_hit=0").fetchall()
        self.assertAlmostEqual(amt, want); self.assertEqual((priced, mine), ("remote", self.b))
        (sb, sst, samt), = self.shared("SELECT batch_id, stage, amount FROM spend WHERE cache_hit=0")
        self.assertEqual((sb, sst), (f"remote:alice:{self.b}", "judge")); self.assertAlmostEqual(samt, want)
        shutil.rmtree(self.C / "cache")                                         # another machine asking the same thing
        n = len(Upstream.seen)
        code, d = self.call("/p/gemini/v1/chat/completions", self.chat("a"))
        self.assertEqual((code, len(Upstream.seen)), (200, n))                  # the shared cache answered
        self.assertEqual(self.con.execute("SELECT amount FROM spend ORDER BY id DESC LIMIT 1").fetchone()[0], 0.0)
        drift = self.open_batch("drift")
        code, d = self.call("/p/gemini/v1/chat/completions", self.chat("a"), batch=drift)
        self.assertEqual((code, len(Upstream.seen)), (200, n + 1))              # a drift batch asks live, there too

    def test_client_cap_on_the_shared_gateway_pauses_the_client_batch(self):
        c = connect(self.S, ensure=False)
        c.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES "
                  "(datetime('now','localtime'), 'remote:alice:old', 'judge', 'gemini', 'm', 0.999, 'table')")
        c.close()
        code, d = self.call("/p/gemini/v1/chat/completions", self.chat("b"))
        self.assertEqual(code, 402)
        self.assertIn("remote client alice", d["error"]["message"])
        self.assertEqual(batches.get(self.con, self.b)["status"], "paused")

    def test_wrong_or_unknown_token_is_refused(self):
        (self.C / "secrets" / "remote.token").write_text("wrong\n")
        code, d = self.call("/p/gemini/v1/chat/completions", self.chat("c"))
        self.assertEqual(code, 401)
        self.assertIn("unknown client or wrong token", json.dumps(d))
        self.assertEqual(gateway.remote_caller("rc|bob|x|b|s|"), "remote client bob: unknown client or wrong token")
        self.assertEqual(self.shared("SELECT COUNT(*) FROM spend")[0][0], 0)

    def test_metered_get_through_the_shared_gateway(self):
        url = f"http://127.0.0.1:{self.srv.server_port}/p/google_maps/maps/api/geocode/json?address=x&key={self.b}:judge"
        with urllib.request.urlopen(url, timeout=20) as r:
            self.assertEqual(json.loads(r.read())["status"], "OK")
        self.assertEqual(self.con.execute("SELECT amount, priced FROM spend").fetchall()[0][:], (0.005, "remote"))
        self.assertEqual(self.shared("SELECT batch_id FROM spend")[0][0], f"remote:alice:{self.b}")

    def test_batch_mode_through_the_shared_gateway(self):
        code, d = self.call("/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("x"), self.chat("y")]})
        self.assertEqual((code, d["submitted"]), (200, 2))
        code, p = self.call(f"/p/gemini/v1/batch/{d['job']}")
        self.assertEqual((p["state"], sorted(p["answers"])), ("done", ["0", "1"]))
        amts = [r[0] for r in self.con.execute("SELECT amount FROM spend WHERE cache_hit=0")]
        self.assertEqual(len(amts), 2)
        self.assertAlmostEqual(amts[0], 0.5 * (1000 * 0.75 + 10 * 3.75) / 1e6)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend_reserve WHERE state='open'").fetchone()[0], 0)
        shutil.rmtree(self.C / "cache")
        code, d2 = self.call("/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("x"), self.chat("z")]})
        self.assertEqual((d2["submitted"], list(d2["cached"])), (1, ["0"]))      # "x" from the shared cache

    def test_add_client_and_join(self):
        os.environ["REINS_HOME"] = str(self.S)
        c = connect(self.S)
        r = remote.add_client(c, "bob", {"daily_cap": 2.0, "weekly_cap": None}, "test")
        self.assertTrue(Path(r["token_file"]).is_file())
        self.assertEqual(oct(Path(r["token_file"]).stat().st_mode & 0o777), "0o600")
        self.assertEqual([x["client"] for x in remote.clients(c)], ["alice", "bob"])
        tok = Path(r["token_file"]).read_text().strip()
        self.assertIsInstance(gateway.remote_caller(f"rc|bob|{tok}|b1|judge|"), dict)
        remote.remove_client(c, "bob", "test")
        self.assertIsInstance(gateway.remote_caller(f"rc|bob|{tok}|b1|judge|"), str)
        c.close()
        J = self.tmp / "joiner"; J.mkdir()
        os.environ["REINS_HOME"] = str(J)
        (self.tmp / "tok").write_text("abc\n")
        jc = connect(J)
        remote.join(jc, f"http://127.0.0.1:{self.port}", "carol", self.tmp / "tok", "test")
        self.assertEqual(providers.remote(), {"gateway": f"http://127.0.0.1:{self.port}", "client": "carol"})
        self.assertEqual(providers.key(providers.get("gemini")), "rc|carol|abc")
        jc.close()


if __name__ == "__main__":
    unittest.main()
