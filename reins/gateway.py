"""C9 paid-call gateway: the only process that holds the OpenRouter key.

    reins gateway serve [--port 8790]

Pipelines point their OpenAI-compatible client at  http://127.0.0.1:<port>/api/v1  and send
    Authorization: Bearer <batch_id>:<stage>
instead of a key. For every /chat/completions request the gateway
  1. checks the batch is running and the stage is a planned stage,
  2. looks the exact request up in the cache ($REINS_HOME/cache, key = sha256 of the canonical body),
  3. otherwise reserves the estimated cost against the caps (C9), forwards with the real key, settles with the
     provider's reported cost (usage.cost; OpenRouter returns it when usage.include=true), else the price table,
  4. on a cap: answers 402 with a message naming the reins command to run, pauses the batch, notifies.
Streaming is refused (the cost is only known at the end; reserve/settle needs the whole answer).

Key: $REINS_HOME/secrets/openrouter.key (one line) or OPENROUTER_API_KEY in the gateway's own environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import batches, notify, spend
from .store import ReinsError, config, connect, home

UPSTREAM = "https://openrouter.ai/api/v1"
# path prefix -> (provider, upstream base). OpenRouter at /api/v1 (its own path), others under /p/<provider>/v1.
PROVIDERS = {"openrouter": "https://openrouter.ai/api/v1", "deepseek": "https://api.deepseek.com/v1"}
KEYS: dict[str, str] = {}
_local = threading.local()


def _con() -> sqlite3.Connection:
    if not hasattr(_local, "con"):
        _local.con = connect()
    return _local.con


def load_key(provider: str = "openrouter", required: bool = True) -> str:
    f = home() / "secrets" / f"{provider}.key"
    if f.is_file():
        return f.read_text().strip()
    k = os.environ.get(f"{provider.upper()}_API_KEY", "")
    if not k and required:
        raise ReinsError(f"no {provider} key: write it to {f} (chmod 600) or set {provider.upper()}_API_KEY for the gateway only")
    return k


def route(path: str) -> tuple[str, str] | None:
    """URL path -> (provider, upstream chat-completions URL)."""
    p = path.rstrip("/")
    if p == "/api/v1/chat/completions":
        return "openrouter", UPSTREAM + "/chat/completions"
    for name, base in PROVIDERS.items():
        if p == f"/p/{name}/v1/chat/completions":
            return name, (UPSTREAM if name == "openrouter" else PROVIDERS[name]) + "/chat/completions"
    return None


def canonical_sha(body: dict) -> str:
    b = {k: v for k, v in body.items() if k not in ("stream", "user", "usage")}
    return hashlib.sha256(json.dumps(b, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cache_path(sha: str):
    return home() / "cache" / sha[:2] / f"{sha}.json"


def cost_of(con, model: str, usage: dict) -> tuple[float, str]:
    if usage.get("cost") is not None:
        return float(usage["cost"]), "provider"
    (pi, po), src = spend.price(con, model)
    return usage.get("prompt_tokens", 0) * pi / 1e6 + usage.get("completion_tokens", 0) * po / 1e6, src


class Handler(BaseHTTPRequestHandler):
    key = ""
    server_version = "reins-gateway/0.1"

    def log_message(self, fmt, *args):                                          # quiet; the ledger is the log
        pass

    def _send(self, code: int, payload: dict | bytes, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code: int, msg: str):
        self._send(code, {"error": {"message": msg, "code": code, "source": "reins-gateway"}})

    def _auth(self) -> tuple[str, str] | None:
        h = self.headers.get("Authorization", "")
        tok = h.removeprefix("Bearer ").strip()
        batch, _, stage = tok.partition(":")
        try:
            b = batches.get(_con(), batch)
        except ReinsError:
            self._err(401, "Authorization must be 'Bearer <batch_id>[:<stage>]' (the gateway holds the real key)")
            return None
        if not stage:                                           # self-staged run: the stage that is running now
            r = _con().execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status='running' ORDER BY ord", (batch,)).fetchall()
            if len(r) != 1:
                self._err(409, f"{batch}: token names no stage and {len(r)} stages are running"); return None
            stage = r[0][0]
        st = _con().execute("SELECT status FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()
        if not st:
            self._err(401, f"stage {stage!r} is not planned in {batch}"); return None
        if b["status"] != "running" or st["status"] != "running":
            self._err(409, f"{batch} is {b['status']}, stage {stage} is {st['status']}: paid calls run only while "
                           f"both are running"); return None
        return batch, stage

    def do_GET(self):
        if self.path.rstrip("/") == "/api/v1/credits":
            req = urllib.request.Request(UPSTREAM + "/credits", headers={"Authorization": "Bearer " + self.key})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self._send(200, r.read())
            except urllib.error.HTTPError as e:
                self._send(e.code, e.read())
            return
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True}); return
        self._err(404, "gateway serves POST /api/v1/chat/completions and GET /api/v1/credits")

    def do_POST(self):
        rt = route(self.path)
        if not rt:
            self._err(404, "POST /api/v1/chat/completions (OpenRouter) or /p/<provider>/v1/chat/completions"); return
        provider, upstream_url = rt
        key = self.key if provider == "openrouter" else KEYS.get(provider) or load_key(provider, required=False)
        if not key:
            self._err(503, f"the gateway has no {provider} key ({home() / 'secrets' / (provider + '.key')})"); return
        auth = self._auth()
        if not auth:
            return
        batch, stage = auth
        con = _con()
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            self._err(400, "body is not JSON"); return
        if body.get("stream"):
            self._err(400, "streaming is not supported through the gateway (cost is settled per whole answer)"); return
        model = body.get("model", "")
        sha = canonical_sha({**body, "_provider": provider})
        mv = con.execute("SELECT module_version FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()[0]
        cp = cache_path(sha)
        drift = con.execute("SELECT type FROM batch WHERE batch_id=?", (batch,)).fetchone()[0] == "drift"
        if cp.is_file() and not drift:                     # a drift batch asks the live model on purpose
            spend.record_free(con, batch, stage, provider=provider, model=model, request_sha=sha, module_version=mv)
            self._send(200, cp.read_bytes()); return
        est = spend.estimate(con, batch, stage, model)
        try:
            rid = spend.reserve(con, batch, stage, est)
        except spend.CapReached as e:
            self._on_cap(con, batch, str(e))
            self._err(402, f"{e}. Batch paused. To continue: reins batch approve {batch} --cap USD && reins ctl resume {batch}")
            return
        except ReinsError as e:
            self._err(409, str(e)); return
        fwd = dict(body)
        if provider == "openrouter":
            fwd["usage"] = {"include": True}                    # OpenRouter reports the charged cost
        fwd.pop("timeout", None)
        req = urllib.request.Request(upstream_url, data=json.dumps(fwd).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                              "HTTP-Referer": "reins-gateway", "X-Title": "reins"})
        try:
            with urllib.request.urlopen(req, timeout=float(body.get("timeout", 180))) as r:
                raw = r.read(); code = 200
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        except (urllib.error.URLError, TimeoutError) as e:
            spend.release(con, rid)
            self._err(504, f"upstream unreachable: {e}"); return
        usage = {}
        try:
            d = json.loads(raw); usage = d.get("usage") or {}
        except json.JSONDecodeError:
            d = None
        amount, priced = cost_of(con, model, usage) if code == 200 else (0.0, "none")
        spend.settle(con, rid, provider=provider, model=model, amount=amount, priced=priced,
                     tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"), cache_hit=False,
                     request_sha=sha, http_status=code, module_version=mv)
        if code == 200 and d is not None:
            if not drift:                                   # drift answers are evidence, not cache
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(raw)
            self._warn_near_cap(con, batch)
        elif code in (402, 403):
            self._on_cap(con, batch, f"provider refused ({code}): balance or key limit -- {raw[:200]!r}")
        self._send(code, raw)

    def _warn_near_cap(self, con, batch):
        b = batches.get(con, batch)
        used = batches.spent(con, batch)
        if b["spend_cap"] and used >= config()["spend_warn_fraction"] * b["spend_cap"]:
            notify.send(con, f"spend80:{batch}", "warn", f"{batch} at ${used:.2f} of ${b['spend_cap']:.2f}",
                        "raise the cap (reins batch approve) or let it stop at the cap")

    def _on_cap(self, con, batch, why):
        try:
            batches.set_status(con, batch, "paused", f"spend: {why}", system=True)
        except ReinsError:
            pass
        notify.send(con, f"cap:{batch}", "action", f"{batch} paused: spend cap or balance reached",
                    f"{why}\nreins batch approve {batch} --cap USD  then  reins ctl resume {batch}", cooldown_min=10)


def serve(port: int | None = None) -> None:
    Handler.key = load_key()
    for name in PROVIDERS:
        if name != "openrouter":
            k = load_key(name, required=False)
            if k:
                KEYS[name] = k
    port = port or config()["gateway_port"]
    (home() / "cache").mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"reins gateway on http://127.0.0.1:{port}/api/v1  (cache {home() / 'cache'})", file=sys.stderr)
    srv.serve_forever()
