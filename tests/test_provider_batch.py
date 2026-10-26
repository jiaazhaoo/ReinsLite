"""Provider batch mode (2026-10-24): chat requests go to the provider as one batch job (Gemini batchGenerateContent),
cached answers come back at once, the rest are reserved, settled at the batch discount on collection and cached."""
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

from reins import batches, gateway, provider_batch, spend
from reins.store import connect


class Upstream(BaseHTTPRequestHandler):
    jobs: dict = {}
    seen: list = []
    ready = True

    def log_message(self, *a):
        pass

    def _out(self, code, d):
        b = json.dumps(d).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Upstream.seen.append((self.path, self.headers.get("x-goog-api-key"), body))
        reqs = body["batch"]["input_config"]["requests"]["requests"]
        Upstream.jobs["batches/j1"] = reqs
        self._out(200, {"name": "batches/j1", "metadata": {"state": "BATCH_STATE_PENDING"}})

    def do_GET(self):
        reqs = Upstream.jobs.get(self.path.lstrip("/"))
        if reqs is None:
            self._out(404, {"error": "no"}); return
        if not Upstream.ready:
            self._out(200, {"name": "batches/j1", "metadata": {"state": "BATCH_STATE_RUNNING"}}); return
        rows = [{"metadata": r["metadata"],
                 "response": {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                                     {"text": json.dumps({"verdict": "correct", "n": i})}]},
                                              "finishReason": "STOP"}],
                              "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10, "thoughtsTokenCount": 100}}}
                for i, r in enumerate(reqs)]
        self._out(200, {"name": "batches/j1", "done": True, "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                        "response": {"inlinedResponses": {"inlinedResponses": rows}}})


class ProviderBatch(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-pbatch-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "owner"
        Upstream.jobs, Upstream.seen, Upstream.ready = {}, [], True
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        (self.tmp / "config.toml").write_text(f'[providers.gemini]\nbase = "http://127.0.0.1:{self.up.server_port}/openai"\n')
        self.con = connect(self.tmp)
        spend.set_price(self.con, "gemini-3.8-flash", 0.75, 3.75, "test")
        gateway.KEYS.clear(); gateway.KEYS.update(gemini="GEKEY")
        self.srv = gateway.Server(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        (self.tmp / "cases.txt").write_text("101\n")
        self.b = batches.open_(self.con, project="p", scope="x-wp1", type_="experiment", purpose="batch mode",
                               case_file=self.tmp / "cases.txt", stages=[batches.parse_stage_spec("judge:paid")], spend_cap=5.0)
        batches.stage_start(self.con, self.b, "judge")

    def tearDown(self):
        self.srv.shutdown(); self.up.shutdown()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.b}:judge", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    @staticmethod
    def chat(text):
        return {"messages": [{"role": "user", "content": [{"type": "text", "text": text},
                                                          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]}],
                "temperature": 0, "response_format": {"type": "json_object"}, "max_tokens": 4000}

    def test_submit_poll_settle_at_the_discount_and_cache(self):
        code, d = self.call("POST", "/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("a"), self.chat("b")]})
        self.assertEqual((code, d["submitted"], d["cached"]), (200, 2, {}))
        path, key, body = Upstream.seen[-1]
        self.assertEqual((path, key), ("/models/gemini-3.8-flash:batchGenerateContent", "GEKEY"))
        r0 = body["batch"]["input_config"]["requests"]["requests"][0]["request"]
        self.assertEqual(r0["contents"][0]["parts"], [{"text": "a"}, {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}}])
        self.assertEqual(r0["generationConfig"], {"temperature": 0, "maxOutputTokens": 4000, "responseMimeType": "application/json"})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend_reserve WHERE state='open'").fetchone()[0], 2)
        Upstream.ready = False
        code, p = self.call("GET", f"/p/gemini/v1/batch/{d['job']}")
        self.assertEqual((code, p["state"]), (200, "pending"))
        Upstream.ready = True
        code, p = self.call("GET", f"/p/gemini/v1/batch/{d['job']}")
        self.assertEqual((p["state"], sorted(p["answers"])), ("done", ["0", "1"]))
        a0 = p["answers"]["0"]
        self.assertEqual(json.loads(a0["choices"][0]["message"]["content"]), {"verdict": "correct", "n": 0})   # no thought text
        self.assertEqual(a0["usage"], {"prompt_tokens": 1000, "completion_tokens": 110, "total_tokens": 1110})
        amts = [r[0] for r in self.con.execute("SELECT amount FROM spend WHERE cache_hit=0")]
        self.assertEqual(len(amts), 2)
        self.assertAlmostEqual(amts[0], 0.5 * (1000 * 0.75 + 110 * 3.75) / 1e6)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend_reserve WHERE state='open'").fetchone()[0], 0)
        code, p2 = self.call("GET", f"/p/gemini/v1/batch/{d['job']}")          # collected once, settled once
        self.assertEqual((p2["answers"], len([1 for _ in self.con.execute("SELECT 1 FROM spend WHERE cache_hit=0")])), (p["answers"], 2))
        code, d2 = self.call("POST", "/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("a"), self.chat("c")]})
        self.assertEqual((d2["submitted"], list(d2["cached"])), (1, ["0"]))      # "a" from the cache, free
        self.assertEqual(d2["cached"]["0"]["choices"], a0["choices"])

    def test_stale_hold_still_books_the_money_and_no_price_refuses(self):
        code, d = self.call("POST", "/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("x")]})
        self.con.execute("UPDATE spend_reserve SET state='released'"); self.con.commit()     # the watchdog's stale sweep
        code, p = self.call("GET", f"/p/gemini/v1/batch/{d['job']}")
        self.assertEqual(p["state"], "done")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM spend WHERE cache_hit=0 AND amount>0").fetchone()[0], 1)
        code, e = self.call("POST", "/p/gemini/v1/batch", {"model": "gemini-unpriced", "requests": [self.chat("y")]})
        self.assertEqual(code, 409)

    def test_other_batch_cannot_collect(self):
        code, d = self.call("POST", "/p/gemini/v1/batch", {"model": "gemini-3.8-flash", "requests": [self.chat("z")]})
        self.assertIsNotNone(provider_batch.load_job(d["job"]))
        self.assertIsNone(provider_batch.load_job("../x"))


if __name__ == "__main__":
    unittest.main()
