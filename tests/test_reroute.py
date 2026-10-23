"""Rerouting (2026-10-23): openrouter's google/gemini-3.8-flash is sent to the direct Gemini key instead, same model.
The cache stays keyed by the request as the client sent it; the original provider answers when the target's own cap
is reached or the target refuses; a target without a price is never used."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reins import batches, gateway, settings_edit, spend
from reins.store import ReinsError, connect


def fake(name: str, cost: bool):
    class Up(BaseHTTPRequestHandler):
        seen: list = []
        fail = 0                                              # answer with this HTTP status instead (0: answer)

        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Up.seen.append((self.headers["Authorization"], body))
            if Up.fail:
                out = json.dumps({"error": {"message": f"{name} says no", "code": Up.fail}}).encode()
                self.send_response(Up.fail)
            else:
                usage = {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1110}
                if cost:
                    usage["cost"] = 0.02
                out = json.dumps({"model": body["model"], "choices": [{"message": {"content": f"from {name}"}}],
                                  "usage": usage}).encode()
                self.send_response(200)
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    return Up


class Reroute(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-reroute-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "owner"
        self.OR, self.GE = fake("openrouter", cost=True), fake("gemini", cost=False)
        self.OR.seen, self.GE.seen = [], []
        self.ups = [ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (self.OR, self.GE)]
        for u in self.ups:
            threading.Thread(target=u.serve_forever, daemon=True).start()
        self.write_config()
        self.con = connect(self.tmp)
        spend.set_price(self.con, "gemini-3.8-flash", 0.75, 3.75, "test")
        gateway.KEYS.clear(); gateway.KEYS.update(openrouter="ORKEY", gemini="GEKEY")
        self.srv = gateway.Server(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        (self.tmp / "cases.txt").write_text("101\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="reroute",
                               case_file=self.tmp / "cases.txt", stages=[batches.parse_stage_spec("judge:paid")], spend_cap=5.0)
        batches.stage_start(self.con, self.b, "judge")

    def write_config(self, extra=""):
        (self.tmp / "config.toml").write_text(
            f'[providers.openrouter]\nbase = "http://127.0.0.1:{self.ups[0].server_port}"\n'
            f'reroute = ["google/gemini-3.8-flash -> gemini/gemini-3.8-flash"]\n{extra}\n'
            f'[providers.gemini]\nbase = "http://127.0.0.1:{self.ups[1].server_port}"\ndaily_cap = 1.0\n')

    def tearDown(self):
        self.srv.shutdown()
        for u in self.ups:
            u.shutdown()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def ask(self, model="google/gemini-3.8-flash", text="hi"):
        body = {"model": model, "messages": [{"role": "user", "content": text}], "temperature": 0,
                "response_format": {"type": "json_object"}, "max_tokens": 100}
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/p/openrouter/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.b}:judge", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def last_spend(self):
        return tuple(self.con.execute("SELECT provider, model, http_status FROM spend ORDER BY id DESC LIMIT 1").fetchone())

    def test_named_model_goes_to_the_target_and_the_cache_still_answers(self):
        code, d = self.ask()
        self.assertEqual((code, d["choices"][0]["message"]["content"]), (200, "from gemini"))
        auth, body = self.GE.seen[-1]
        self.assertEqual((auth, body["model"]), ("Bearer GEKEY", "gemini-3.8-flash"))
        self.assertNotIn("usage", body)                          # openrouter's extra_body is not sent to gemini
        self.assertEqual((body["temperature"], body["response_format"], body["max_tokens"]), (0, {"type": "json_object"}, 100))
        self.assertEqual(self.last_spend(), ("gemini", "gemini-3.8-flash", 200))
        amt = self.con.execute("SELECT amount FROM spend ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertAlmostEqual(amt, 1000 * 0.75 / 1e6 + 110 * 3.75 / 1e6)     # thinking tokens (total - prompt) as output
        code, d = self.ask()                                         # the same request: a cache hit, nobody asked
        self.assertEqual((code, len(self.GE.seen), len(self.OR.seen)), (200, 1, 0))
        code, d = self.ask(model="anthropic/other")                  # not named: stays on openrouter
        self.assertEqual((code, d["choices"][0]["message"]["content"], self.OR.seen[-1][1]["model"]), (200, "from openrouter", "anthropic/other"))
        self.assertEqual(self.OR.seen[-1][1]["usage"], {"include": True})

    def test_target_refusal_or_cap_falls_back_to_the_original(self):
        self.GE.fail = 402                                           # prepay balance gone
        code, d = self.ask(text="a")
        self.assertEqual((code, d["choices"][0]["message"]["content"]), (200, "from openrouter"))
        self.assertEqual(self.OR.seen[-1][1]["model"], "google/gemini-3.8-flash")
        self.assertEqual(batches.get(self.con, self.b)["status"], "running")   # a refused reroute does not pause the batch
        self.assertTrue(self.con.execute("SELECT 1 FROM notification WHERE key='reroute:google/gemini-3.8-flash'").fetchone())
        self.GE.fail = 0
        import datetime as dt
        self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?, 'judge', 'gemini', 'm', 0.99, 'table')",
                         (dt.datetime.now().isoformat(timespec="seconds"), self.b)); self.con.commit()
        n = len(self.GE.seen)
        code, d = self.ask(text="b")                                 # gemini's daily cap $1 is spent: openrouter answers
        self.assertEqual((code, d["choices"][0]["message"]["content"], len(self.GE.seen)), (200, "from openrouter", n))
        self.assertEqual(self.last_spend(), ("openrouter", "google/gemini-3.8-flash", 200))

    def test_no_fallback_and_no_price(self):
        self.write_config("reroute_fallback = false")
        self.GE.fail = 402
        code, d = self.ask(text="c")
        self.assertEqual(code, 402)
        self.assertEqual(batches.get(self.con, self.b)["status"], "paused")
        self.assertEqual(len(self.OR.seen), 0)
        self.write_config()
        batches.set_status(self.con, self.b, "running", "test")
        self.con.execute("DELETE FROM price WHERE model='gemini-3.8-flash'"); self.con.commit()
        self.GE.fail = 0
        code, d = self.ask(text="d")                                 # a target without a price is never used
        self.assertEqual((code, d["choices"][0]["message"]["content"]), (200, "from openrouter"))

    def test_config_set_reroute(self):
        settings_edit.set_(self.con, "providers.openrouter.reroute",
                           '["google/gemini-3.8-flash -> gemini/gemini-3.8-flash", "google/gemini-2.5-flash -> gemini"]', "use the direct key")
        from reins import providers
        spec = providers.get("openrouter")
        self.assertEqual(providers.reroute(spec, "google/gemini-2.5-flash")[1], "google/gemini-2.5-flash")
        self.assertEqual(providers.reroute(spec, "google/gemini-3.8-flash")[0]["name"], "gemini")
        self.assertIsNone(providers.reroute(spec, "x"))
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "providers.openrouter.reroute", '["no arrow"]', "bad")
        settings_edit.set_(self.con, "providers.openrouter.reroute_fallback", "false", "test")
        self.assertIs(providers.get("openrouter")["reroute_fallback"], False)


if __name__ == "__main__":
    unittest.main()
