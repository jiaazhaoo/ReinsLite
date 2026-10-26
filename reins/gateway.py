"""C9 paid-call gateway: the only process that holds the provider keys (which providers: reins/providers.py).

    reins gateway serve [--port 8790]

Pipelines point their client at  http://127.0.0.1:<port>/p/<provider>/v1  (chat) or  /p/<provider>  (GET services;
`reins run` sets <ENV>_BASE_URL for every provider) and send
    Authorization: Bearer <batch_id>:<stage>      (GET services: the same token as their key parameter)
instead of a key. For every /chat/completions request the gateway
  1. checks the batch is running and the stage is a planned stage,
  2. looks the exact request up in the cache ($REINS_HOME/cache, key = sha256 of the canonical body),
  3. otherwise reserves the estimated cost against the caps (C9), forwards with the real key, settles with the
     provider's reported cost (usage.cost; OpenRouter returns it when usage.include=true), else the price table;
     a model with neither (no price, provider does not report costs) is refused, the batch paused, someone told;
     an identical request already in flight waits for that answer instead of being paid twice,
     A provider may reroute named models to another provider (config.toml [providers.<name>] reroute, see
     reins/providers.py): the answer is cached under the request as the client sent it, and the original provider
     answers when the target's cap is reached or it refuses (unless reroute_fallback = false).
  4. on a cap (the batch's, its stage's, the pools', or the provider's own daily_cap / weekly_cap / hourly_cap in
     config.toml [providers.<name>]): answers 402 with a message naming the reins command to run, pauses the batch,
     notifies.
Streaming is refused (the cost is only known at the end; reserve/settle needs the whole answer).

Keys: $REINS_HOME/secrets/<key_file>.key (one line, chmod 600) or <ENV>_API_KEY in the gateway's own environment.

Shared gateway (reins/remote.py): with gateway_host = "0.0.0.0" other machines' gateways call this one with a remote
client token rc|<client>|<secret>|<batch>|<stage>|<flags>; such a call is held against that client's own caps
([remote_clients.<client>]) and the providers' caps, shares the cache, is booked as remote:<client>:<batch>, and its
answer carries X-Reins-Cost (what was booked; 0 from the cache). From another machine nothing else is accepted.
A machine with [remote] in its config.toml is such a client: its gateway forwards to the shared one instead of to
the providers, keeping its own batches, caps, cache and ledger.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import batches, notify, provider_batch, providers, spend
from .store import ReinsError, config, connect, home

KEYS: dict[str, str] = {}
_local = threading.local()
_FAILS: dict[tuple[str, str], int] = {}       # (batch, stage) -> consecutive non-200 upstream answers
_INFLIGHT: dict[str, threading.Event] = {}    # request sha -> set when the call that owns it has finished
_INFLIGHT_LOCK = threading.Lock()


_STARTED: dict[str, float] = {}               # request sha -> when its owner started (the deep health check reports the oldest)
MAX_ACTIVE = 64                               # requests handled at once; more wait up to WAIT_S, then get 503 (no thread pile-up)
WAIT_S = 30
_ACTIVE = threading.BoundedSemaphore(MAX_ACTIVE)


def _claim(sha: str) -> threading.Event | None:
    """None: this thread owns the call for `sha`. An Event: the same request is already in flight; wait for it.
    (2026-10-10: 26 + 20 identical judge requests were forwarded and paid twice, seconds apart.)"""
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(sha)
        if ev is None:
            _INFLIGHT[sha] = threading.Event()
            _STARTED[sha] = time.time()
        return ev


def _done(sha: str) -> None:
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.pop(sha, None)
        _STARTED.pop(sha, None)
    if ev:
        ev.set()


def _con() -> sqlite3.Connection:
    """The registry connection of the request being handled (opened on first use, closed by Handler.handle)."""
    if getattr(_local, "con", None) is None:
        _local.con = connect(ensure=False)        # the schema was ensured once at start (serve)
    return _local.con


def _close_con() -> None:
    """ThreadingHTTPServer runs every client connection on a new thread. Nothing closed the thread's sqlite
    connection, and a dead thread's connection is not reliably freed: 2026-10-14 the gateway held 986 of 1024 fds
    (495 on reins.db, 470 on reins.db-wal), then every request failed with EMFILE and the judges lost answers."""
    con = getattr(_local, "con", None)
    _local.con = None
    if con is not None:
        con.close()


def load_key(spec: dict) -> str:
    return KEYS.get(spec["name"]) or providers.key(spec)


CLIENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def client_token_path(client: str):
    return home() / "secrets" / "clients" / f"{client}.token"


def remote_caller(tok: str) -> dict | str:
    """A remote client's credential rc|<client>|<secret>|<batch>|<stage>|<flags> -> who it is, or why it is refused.
    The client must have a token file (secrets/clients/<client>.token) and a [remote_clients.<client>] section."""
    parts = tok.split("|")
    if len(parts) != 6 or parts[0] != providers.REMOTE_TOKEN:
        return "a remote token is rc|<client>|<secret>|<batch>|<stage>|<flags>"
    _, client, secret, batch, stage, flags = parts
    if not CLIENT_NAME.fullmatch(client) or not batch or not stage:
        return "remote token: bad client name, batch or stage"
    f = client_token_path(client)
    if not f.is_file() or not hmac.compare_digest(f.read_text().strip(), secret):
        return f"remote client {client}: unknown client or wrong token"
    try:
        spend.client_limits(client)
    except ReinsError as e:
        return str(e)
    return {"client": client, "batch": batch, "stage": stage, "drift": "d" in flags, "id": spend.remote_id(client, batch)}


def route(path: str) -> tuple[dict, str] | None:
    """URL path -> (chat provider, upstream chat-completions URL). /api/v1 is the default chat provider (older clients)."""
    p = path.rstrip("/")
    if p == "/api/v1/chat/completions":
        d = providers.default_chat()
        return (d, d["base"].rstrip("/") + "/chat/completions") if d else None
    for spec in providers.all_().values():
        if spec["kind"] == "chat" and p == f"/p/{spec['name']}/v1/chat/completions":
            return spec, spec["base"].rstrip("/") + "/chat/completions"
    return None


def get_route(path: str) -> dict | None:
    """URL path -> the GET-kind provider it belongs to (/p/<name>/...)."""
    for spec in providers.all_().values():
        if spec["kind"] == "get" and path.startswith(f"/p/{spec['name']}/"):
            return spec
    return None


def canonical_sha(body: dict) -> str:
    b = {k: v for k, v in body.items() if k not in ("stream", "user", "usage")}
    return hashlib.sha256(json.dumps(b, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cache_path(sha: str):
    return home() / "cache" / sha[:2] / f"{sha}.json"


def tokens_out(usage: dict) -> int | None:
    """Billed output tokens. Gemini's OpenAI-compatible answers leave thinking tokens out of completion_tokens and only
    count them in total_tokens (2026-10-23: completion 3, prompt 14, total 413), yet bill them as output."""
    out = usage.get("completion_tokens")
    if usage.get("total_tokens") is not None and usage.get("prompt_tokens") is not None:
        out = max(out or 0, usage["total_tokens"] - usage["prompt_tokens"])
    return out


def cost_of(con, model: str, usage: dict) -> tuple[float, str]:
    if usage.get("cost") is not None:
        return float(usage["cost"]), "provider"
    (pi, po), src = spend.price(con, model)
    return usage.get("prompt_tokens", 0) * pi / 1e6 + (tokens_out(usage) or 0) * po / 1e6, src


def cap_message(batch: str, e: spend.CapReached) -> str:
    if isinstance(e, spend.ProviderCapReached):
        return f"{e}. Batch paused. To continue once the window has room or the cap is raised: reins ctl resume {batch}"
    return f"{e}. Batch paused. To continue: reins batch approve {batch} --cap USD && reins ctl resume {batch}"


class Handler(BaseHTTPRequestHandler):
    key = ""
    server_version = "reins-gateway/0.1"
    _rc: dict | None = None                       # the remote client of the request being handled (remote_caller)

    # a remote client's batch has no row here: its drift flag comes with the token, it has no module version, its
    # spend is held against its own caps (spend.reserve_remote) and every answer says what was booked (X-Reins-Cost)
    def _drift(self, con, batch) -> bool:
        return self._rc["drift"] if self._rc else batches.get(con, batch)["type"] == "drift"

    def _mv(self, con, batch, stage):
        if self._rc:
            return None
        return con.execute("SELECT module_version FROM batch_stage WHERE batch_id=? AND stage=?", (batch, stage)).fetchone()[0]

    def _reserve(self, con, batch, stage, amount, provider):
        if self._rc:
            return spend.reserve_remote(con, self._rc["client"], self._rc["batch"], stage, amount, provider=provider)
        return spend.reserve(con, batch, stage, amount, provider=provider)

    def _local_peer(self) -> bool:
        """A caller on this machine. Batch tokens are batch ids anyone can read off the board: from another machine
        only a remote client's token (rc|...) is accepted (config gateway_host opens the gateway to the network)."""
        return self.client_address[0] in ("127.0.0.1", "::1") or self.client_address[0].startswith("127.")

    def _cost(self, amount) -> dict | None:
        return {"X-Reins-Cost": repr(float(amount or 0))} if self._rc else None

    def log_message(self, fmt, *args):                                          # quiet; the ledger is the log
        pass

    def handle(self):
        if not _ACTIVE.acquire(timeout=WAIT_S):            # saturated (or stuck): answer at once instead of piling up
            try:
                self.rfile.readline(65537)
                body = b'{"error": {"message": "gateway busy: too many requests at once; retry", "code": 503}}'
                self.wfile.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\n"
                                 b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            return
        try:
            super().handle()
        finally:
            _ACTIVE.release()
            _close_con()

    def _send(self, code: int, payload: dict | bytes, ctype="application/json", headers: dict | None = None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code: int, msg: str):
        self._send(code, {"error": {"message": msg, "code": code, "source": "reins-gateway"}})

    def _auth(self) -> tuple[str, str] | None:
        h = self.headers.get("Authorization", "")
        tok = h.removeprefix("Bearer ").strip()
        self._rc = None
        if tok.startswith(providers.REMOTE_TOKEN + "|"):
            rc = remote_caller(tok)
            if isinstance(rc, str):
                self._err(401, rc); return None
            self._rc = rc
            return rc["id"], rc["stage"]
        if not self._local_peer():
            self._err(401, "from another machine the gateway takes only a remote client token (reins gateway client add)")
            return None
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

    def _metered_get(self, spec: dict):
        """GET /p/<name>/<path>?...&<key_param>=<batch[:stage]> -> <base>/<path> with the real key, priced per call."""
        from urllib.parse import parse_qsl, urlencode, urlsplit
        u = urlsplit(self.path)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        kp = spec.get("key_param", "key")
        tok = q.pop(kp, "") or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        con = _con()
        self._rc = None
        if tok.startswith(providers.REMOTE_TOKEN + "|"):
            rc = remote_caller(tok)
            if isinstance(rc, str):
                self._err(401, rc); return
            self._rc = rc
            batch, stage = rc["id"], rc["stage"]
        elif not self._local_peer():
            self._err(401, "from another machine the gateway takes only a remote client token (reins gateway client add)"); return
        else:
            batch, _, stage = tok.partition(":")
            try:
                b = batches.get(con, batch)
            except ReinsError:
                self._err(401, f"{kp} must be the batch token <batch_id>[:<stage>] (the gateway holds the {spec['name']} key)"); return
            if not stage:
                r = con.execute("SELECT stage FROM batch_stage WHERE batch_id=? AND status='running' ORDER BY ord", (batch,)).fetchall()
                if len(r) != 1:
                    self._err(409, f"{batch}: token names no stage and {len(r)} stages are running"); return
                stage = r[0][0]
            if b["status"] != "running":
                self._err(409, f"{batch} is {b['status']}: paid calls run only while it is running"); return
        drift = self._drift(con, batch)
        key = load_key(spec)
        led = spec["ledger"]
        if not key:
            self._err(503, f"the gateway has no {spec['name']} key ({home() / 'secrets' / (spec['key_file'] + '.key')})"); return
        rest = u.path.removeprefix(f"/p/{spec['name']}")
        api = rest.removeprefix(spec.get("path_prefix", "/")).removesuffix("/json").strip("/")
        price = next((p for k, p in (spec.get("per_call") or {}).items() if api.startswith(k)), float(spec.get("default_call", 0)))
        sha = hashlib.sha256((spec["name"] + "|" + api + "|" + urlencode(sorted(q.items()))).encode()).hexdigest()
        mv = self._mv(con, batch, stage)
        cp = cache_path(sha)
        if cp.is_file() and not drift:
            spend.record_free(con, batch, stage, provider=led, model=api, request_sha=sha, module_version=mv)
            self._send(200, cp.read_bytes(), headers=self._cost(0)); return
        try:
            rid = self._reserve(con, batch, stage, price, led)
        except spend.CapReached as e:
            self._on_cap(con, batch, str(e))
            self._err(402, cap_message(batch, e)); return
        except ReinsError as e:
            self._err(409, str(e)); return
        if spec.get("via") == "remote":                    # this machine has no key: the shared gateway's credential
            key = providers.remote_token(batch, stage, drift, key)
        url = spec["base"].rstrip("/") + rest + "?" + urlencode({**q, kp: key})
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw, code, hc = r.read(), 200, r.headers.get("X-Reins-Cost")
        except urllib.error.HTTPError as e:
            raw, code, hc = e.read(), e.code, e.headers.get("X-Reins-Cost")
        except (urllib.error.URLError, TimeoutError) as e:
            spend.release(con, rid); self._err(504, f"upstream unreachable: {e}"); return
        # a metered GET is billed whatever the answer says (a geocode with ZERO_RESULTS is still a call)
        amount, priced = (float(hc), "remote") if hc is not None else (price if code == 200 else 0.0, "table")
        spend.settle(con, rid, provider=led, model=api, amount=amount, priced=priced,
                     tokens_in=None, tokens_out=None, cache_hit=False, request_sha=sha, http_status=code, module_version=mv)
        if code == 200 and not drift:
            cp.parent.mkdir(parents=True, exist_ok=True); cp.write_bytes(raw)
            self._warn_near_cap(con, batch)
        self._send(code, raw, headers=self._cost(amount))

    def do_GET(self):
        bs = self._batch_spec()
        if bs and bs[1]:
            self._batch_poll(*bs); return
        spec = get_route(self.path)
        if spec:
            self._metered_get(spec); return
        d = providers.default_chat()
        if self.path.rstrip("/") == "/api/v1/credits" and d and d.get("balance") and self._local_peer():
            req = urllib.request.Request(d["balance"]["url"], headers={"Authorization": "Bearer " + load_key(d)})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self._send(200, r.read())
            except urllib.error.HTTPError as e:
                self._send(e.code, e.read())
            return
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True}); return
        if self.path.rstrip("/") == "/health/deep":           # what a stuck gateway cannot answer (reins/watchdog.py)
            t0 = time.time()
            got = _INFLIGHT_LOCK.acquire(timeout=3)
            if got:
                oldest = min(_STARTED.values(), default=None)
                n_inflight = len(_INFLIGHT)
                _INFLIGHT_LOCK.release()
            c = connect(ensure=False)
            try:
                c.execute("SELECT 1 FROM batch LIMIT 1").fetchone()
            finally:
                c.close()
            self._send(200 if got else 503, {"ok": got, "threads": threading.active_count(), "inflight": n_inflight if got else None,
                                             "oldest_inflight_s": round(time.time() - oldest) if got and oldest else 0,
                                             "busy": MAX_ACTIVE - _ACTIVE._value, "check_ms": round(1000 * (time.time() - t0))})
            return
        self._err(404, "gateway serves POST /p/<provider>/v1/chat/completions and GET /p/<provider>/... (reins/providers.py)")

    def _batch_spec(self) -> tuple[dict, str] | None:
        """/p/<name>/v1/batch[/<job>] of a provider with a batch adapter -> (spec, job id or '')."""
        for spec in providers.all_().values():
            pre = f"/p/{spec['name']}/v1/batch"
            if spec.get("batch") in provider_batch.ADAPTERS and (self.path.rstrip("/") == pre or self.path.startswith(pre + "/")):
                return spec, self.path[len(pre):].strip("/")
        return None

    def _batch_submit(self, spec: dict):
        """POST /p/<name>/v1/batch {"model", "requests": [chat body...]}: cached answers at once, the rest as one job."""
        key = load_key(spec)
        if not key:
            self._err(503, f"the gateway has no {spec['name']} key"); return
        auth = self._auth()
        if not auth:
            return
        batch, stage = auth
        con = _con()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except json.JSONDecodeError:
            self._err(400, "body is not JSON"); return
        model, reqs = body.get("model", ""), body.get("requests") or []
        why = spend.unpriced(con, spec, model)
        if why:
            self._on_unpriced(con, batch, model, why); self._err(409, why); return
        mv = self._mv(con, batch, stage)
        drift = self._drift(con, batch)
        cached, todo = {}, []
        for i, rb in enumerate(reqs):
            rb = {**rb, "model": model}
            sha = canonical_sha({**rb, "_provider": spec["ledger"]})
            cp = cache_path(sha)
            if cp.is_file() and not drift:
                spend.record_free(con, batch, stage, provider=spec["ledger"], model=model, request_sha=sha, module_version=mv)
                cached[str(i)] = json.loads(cp.read_bytes())
            else:
                todo.append((i, sha, rb))
        if not todo:
            self._send(200, {"job": None, "cached": cached, "submitted": 0}); return
        disc = float(spec.get("batch_discount", 1.0))
        est = spend.estimate(con, batch, stage, model) * disc
        rids = []
        try:
            for _ in todo:
                rids.append(self._reserve(con, batch, stage, est, spec["ledger"]))
        except (spend.CapReached, ReinsError) as e:
            for r in rids:
                spend.release(con, r)
            if isinstance(e, spend.CapReached):
                self._on_cap(con, batch, str(e)); self._err(402, cap_message(batch, e))
            else:
                self._err(409, str(e))
            return
        if spec.get("via") == "remote":
            code, op = self._remote_batch(spec, key, batch, stage, drift, data={"model": model, "requests": [rb for _, _, rb in todo]})
            if code == 200 and isinstance(op.get("cached"), dict):     # answered from the shared gateway's cache: booked 0 there
                left = []
                for j, ((i, sha, _), rid) in enumerate(zip(todo, rids)):
                    ans = op["cached"].get(str(j))
                    if ans is None:
                        left.append((i, sha, rid, j)); continue
                    self._settle_answer(con, spec, model, rid, sha, mv, ans, 0.0, "remote", drift)
                    cached[str(i)] = ans
                if not op.get("job"):
                    self._send(200, {"job": None, "cached": cached, "submitted": 0}); return
                op = {"name": op["job"], "items": left}
        else:
            sub, _ = provider_batch.ADAPTERS[spec["batch"]]
            code, op = sub(spec, key, model, [(sha, rb) for _, sha, rb in todo])
        if code != 200 or not op.get("name"):
            for r in rids:
                spend.release(con, r)
            if code == 402:                                # the shared gateway's cap for this client, or its provider's
                self._on_cap(con, batch, f"shared gateway: {json.dumps(op)[:300]}")
            self._send(code if code != 200 else 502, {"error": {"message": f"batch submit refused: {json.dumps(op)[:400]}",
                                                                  "code": code, "source": "reins-gateway"}}); return
        items = op.get("items") or [[i, sha, rid, j] for j, ((i, sha, _), rid) in enumerate(zip(todo, rids))]
        jid = provider_batch.new_job({"provider": spec["name"], "name": op["name"], "batch": batch, "stage": stage,
                                      "model": model, "mv": mv, "drift": drift, "discount": disc, "via": spec.get("via"),
                                      "rc": {"client": self._rc["client"], "batch": self._rc["batch"]} if self._rc else None,
                                      "items": [list(x) for x in items]})
        self._send(200, {"job": jid, "cached": cached, "submitted": len(items)})

    def _remote_batch(self, spec, key, batch, stage, drift, data=None, job=""):
        """Submit to / poll the shared gateway's batch endpoint (this machine has no key): (HTTP status, its answer)."""
        url = spec["base"].rstrip("/") + "/batch" + (f"/{job}" if job else "")
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Authorization": "Bearer " + providers.remote_token(batch, stage, drift, key),
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except json.JSONDecodeError:
                return e.code, {"error": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            return 504, {"error": f"shared gateway unreachable: {e}"}

    def _settle_answer(self, con, spec, model, rid, sha, mv, ans, amount, priced, drift, job=None):
        """Book one batch answer on its hold (a new $0 hold when the watchdog released the old one: the money was spent)
        and cache it."""
        u = ans.get("usage") or {}
        kw = dict(provider=spec["ledger"], model=model, amount=amount, priced=priced, tokens_in=u.get("prompt_tokens"),
                  tokens_out=u.get("completion_tokens"), cache_hit=False, request_sha=sha, http_status=200, module_version=mv)
        try:
            spend.settle(con, rid, **kw)
        except ReinsError:
            rc = (job or {}).get("rc")
            b, st = ((job or {}).get("batch"), (job or {}).get("stage")) if job else (None, None)
            hold = (spend.reserve_remote(con, rc["client"], rc["batch"], st, 0.0, provider=spec["ledger"]) if rc
                    else spend.reserve(con, b, st, 0.0, provider=spec["ledger"]))
            spend.settle(con, hold, **kw)
        if not drift:
            cp = cache_path(sha); cp.parent.mkdir(parents=True, exist_ok=True); cp.write_bytes(json.dumps(ans).encode())

    def _batch_poll(self, spec: dict, jid: str):
        """GET /p/<name>/v1/batch/<job>: pending, or the answers (settled and cached once, on the first collection)."""
        auth = self._auth()
        if not auth:
            return
        job = provider_batch.load_job(jid)
        if not job or job["batch"] != auth[0]:
            self._err(404, f"no batch job {jid} of {auth[0]}"); return
        if job.get("answers") is not None:
            self._send(200, {"state": "done", "answers": job["answers"], "costs": job.get("costs") or {}}); return
        con = _con()
        answers, costs = {}, {}
        if job.get("via") == "remote":
            code, got = self._remote_batch(spec, load_key(spec), job["batch"], job["stage"], job["drift"], job=job["name"])
            state = got.get("state") if code == 200 else "pending"     # unreachable for now: ask again later
            if state == "pending":
                self._send(200, {"state": "pending", "provider_state": got.get("provider_state") or got.get("error")}); return
            pstate = got.get("provider_state")
            for i, sha, rid, j in job["items"]:
                ans = (got.get("answers") or {}).get(str(j))
                if state != "done" or ans is None:
                    spend.release(con, rid); continue
                c = float((got.get("costs") or {}).get(str(j), 0.0))
                self._settle_answer(con, spec, job["model"], rid, sha, job["mv"], ans, c, "remote", job["drift"], job)
                answers[str(i)], costs[str(i)] = ans, c
        else:
            _, pol = provider_batch.ADAPTERS[spec["batch"]]
            state, got, pstate = pol(spec, load_key(spec), job["name"])
            if state == "pending":
                self._send(200, {"state": "pending", "provider_state": pstate}); return
            for i, sha, rid, *_ in job["items"]:
                nat = (got or {}).get(sha)
                if state != "done" or not nat or "error" in nat and "candidates" not in nat:
                    spend.release(con, rid); continue
                ans = provider_batch.from_native(nat, job["model"])
                amount, priced = cost_of(con, job["model"], ans["usage"])
                self._settle_answer(con, spec, job["model"], rid, sha, job["mv"], ans, amount * job["discount"], priced,
                                    job["drift"], job)
                answers[str(i)], costs[str(i)] = ans, amount * job["discount"]
        job["answers"], job["costs"], job["state"] = answers, costs, state
        provider_batch.save_job(jid, job)
        self._send(200, {"state": state, "provider_state": pstate, "answers": answers, "costs": costs})

    def do_POST(self):
        bs = self._batch_spec()
        if bs:
            self._batch_submit(bs[0]); return
        rt = route(self.path)
        if not rt:
            self._err(404, "POST /p/<provider>/v1/chat/completions (or /api/v1/chat/completions for the default)"); return
        spec, upstream_url = rt
        provider = spec["ledger"]
        key = load_key(spec)
        if not key:
            self._err(503, f"the gateway has no {spec['name']} key ({home() / 'secrets' / (spec['key_file'] + '.key')})"); return
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
        mv = self._mv(con, batch, stage)
        cp = cache_path(sha)
        drift = self._drift(con, batch)
        if cp.is_file() and not drift:                     # a drift batch asks the live model on purpose
            spend.record_free(con, batch, stage, provider=provider, model=model, request_sha=sha, module_version=mv)
            self._send(200, cp.read_bytes(), headers=self._cost(0)); return
        why = spend.unpriced(con, spec, model)
        if why:                                            # never book a guess: refuse, pause, tell someone
            self._on_unpriced(con, batch, model, why)
            self._err(409, f"{why}. Batch paused; after the price is set: reins ctl resume {batch}"); return
        if not drift:
            ev = _claim(sha)
            if ev is not None:                             # the same request is on its way: wait, then take its answer
                ev.wait(float(body.get("timeout", 180)) + 10)
                if cp.is_file():
                    spend.record_free(con, batch, stage, provider=provider, model=model, request_sha=sha, module_version=mv)
                    self._send(200, cp.read_bytes(), headers=self._cost(0)); return
                ev = _claim(sha)                           # it failed: ask again, owning the call this time
                if ev is not None:
                    self._err(503, "the same request is still in flight; retry"); return
        try:
            self._forward(con, spec, upstream_url, provider, key, batch, stage, body, model, sha, mv, cp, drift)
        finally:
            if not drift:
                _done(sha)

    def _upstream(self, spec, url, key, body, model, batch="", stage="", drift=False) -> tuple[bytes | None, int | str, str | None]:
        """One POST to a chat provider: (raw answer, HTTP status, X-Reins-Cost of a shared gateway), or (None, why, None)
        when it could not be reached."""
        fwd = {**body, "model": model, **(spec.get("extra_body") or {})}   # e.g. OpenRouter: usage.include -> the charged cost
        if spec.get("via") != "remote":
            fwd.pop("timeout", None)                       # the shared gateway reads it too
        else:
            key = providers.remote_token(batch, stage, drift, key)
        req = urllib.request.Request(url, data=json.dumps(fwd).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                              "HTTP-Referer": "reins-gateway", "X-Title": "reins"})
        try:
            with urllib.request.urlopen(req, timeout=float(body.get("timeout", 180)) + (15 if spec.get("via") == "remote" else 0)) as r:
                return r.read(), 200, r.headers.get("X-Reins-Cost")
        except urllib.error.HTTPError as e:
            return e.read(), e.code, e.headers.get("X-Reins-Cost")
        except (urllib.error.URLError, TimeoutError) as e:
            return None, str(e), None

    def _routes(self, con, spec, upstream_url, provider, key, model) -> list[tuple]:
        """[(spec, url, ledger, key, model)] to try in order: a reroute target first (providers.reroute), then the
        provider the client asked for unless reroute_fallback = false. A target without a key or a price is skipped."""
        own = (spec, upstream_url, provider, key, model)
        alt = providers.reroute(spec, model)
        if not alt:
            return [own]
        tspec, tmodel = alt
        tkey = load_key(tspec)
        why = f"the gateway has no {tspec['name']} key" if not tkey else spend.unpriced(con, tspec, tmodel)
        if why:
            notify.send(con, f"reroute:{model}", "warn", f"{model} not rerouted to {tspec['name']}", why, cooldown_min=60)
            return [own]
        target = (tspec, tspec["base"].rstrip("/") + "/chat/completions", tspec["ledger"], tkey, tmodel)
        return [target, own] if spec.get("reroute_fallback", True) else [target]

    def _forward(self, con, spec, upstream_url, provider, key, batch, stage, body, model, sha, mv, cp, drift):
        est = spend.estimate(con, batch, stage, model)
        routes = self._routes(con, spec, upstream_url, provider, key, model)
        for i, (rspec, url, led, rkey, rmodel) in enumerate(routes):
            last = i == len(routes) - 1
            try:
                rid = self._reserve(con, batch, stage, est, led)
            except spend.ProviderCapReached as e:
                if not last:                               # the reroute target's own cap: the original provider takes it
                    continue
                self._on_cap(con, batch, str(e)); self._err(402, cap_message(batch, e)); return
            except spend.CapReached as e:
                self._on_cap(con, batch, str(e)); self._err(402, cap_message(batch, e)); return
            except ReinsError as e:
                self._err(409, str(e)); return
            raw, code, hc = self._upstream(rspec, url, rkey, body, rmodel, batch, stage, drift)
            if raw is None:
                spend.release(con, rid)
                if not last:
                    continue
                self._err(504, f"upstream unreachable: {code}"); return
            if code != 200 and not last:                   # the target refused (no credits, rate limit, a parameter...)
                spend.settle(con, rid, provider=led, model=rmodel, amount=0.0, priced="none", tokens_in=None, tokens_out=None,
                             cache_hit=False, request_sha=sha, http_status=code, module_version=mv)
                notify.send(con, f"reroute:{model}", "warn", f"{rspec['name']} refused {rmodel} ({code}); {provider} answered",
                            raw[:300].decode("utf-8", "replace"), cooldown_min=60)
                continue
            self._finish(con, rspec, led, rmodel, batch, stage, rid, raw, code, sha, mv, cp, drift, hc)
            return

    def _finish(self, con, spec, provider, model, batch, stage, rid, raw, code, sha, mv, cp, drift, hc=None):
        usage = {}
        try:
            d = json.loads(raw); usage = d.get("usage") or {}
        except json.JSONDecodeError:
            d = None
        if hc is not None:                                  # a shared gateway booked it: its amount, 0 on its cache
            amount, priced = float(hc), "remote"
        else:
            amount, priced = cost_of(con, model, usage) if code == 200 else (0.0, "none")
        spend.settle(con, rid, provider=provider, model=model, amount=amount, priced=priced,
                     tokens_in=usage.get("prompt_tokens"), tokens_out=tokens_out(usage), cache_hit=False,
                     request_sha=sha, http_status=code, module_version=mv)
        if priced == "default":
            notify.send(con, f"unpriced:{model}", "action", f"{model}: cost guessed at the default price",
                        f"{provider} reported no cost for {model} and it has no price; booked at "
                        f"${spend.DEFAULT_PRICE[0]}/${spend.DEFAULT_PRICE[1]} per million tokens.\n"
                        f"reins spend price {model} IN OUT, then decide on reins spend reprice {model} --why ...")
        key_ = (batch, stage)
        if code == 200:
            _FAILS[key_] = 0
        else:
            _FAILS[key_] = _FAILS.get(key_, 0) + 1
            if _FAILS[key_] >= config()["pool"]["max_consecutive_failures"]:
                self._on_cap(con, batch, f"{_FAILS[key_]} paid calls failed in a row (last HTTP {code}): not retrying on money")
        if code == 200 and d is not None:
            if not drift:                                   # drift answers are evidence, not cache
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(raw)
            self._warn_near_cap(con, batch)
        elif code in (402, 403):
            self._on_cap(con, batch, f"provider refused ({code}): balance or key limit -- {raw[:200]!r}")
        self._send(code, raw, headers=self._cost(amount))

    def _warn_near_cap(self, con, batch):
        if self._rc:                                       # a remote client: its own reins watches its batch caps
            return
        b = batches.get(con, batch)
        used = batches.spent(con, batch)
        if b["spend_cap"] and used >= config()["spend_warn_fraction"] * b["spend_cap"]:
            notify.send(con, f"spend80:{batch}", "warn", f"{batch} at ${used:.2f} of ${b['spend_cap']:.2f}",
                        "raise the cap (reins batch approve) or let it stop at the cap")

    def _on_unpriced(self, con, batch, model, why):
        if self._rc:
            notify.send(con, f"unpriced:{model}", "action", f"remote client {self._rc['client']}: no price for {model}", why,
                        cooldown_min=10)
            return
        try:
            batches.set_status(con, batch, "paused", f"spend: {why}"[:500], system=True)
        except ReinsError:
            pass
        notify.send(con, f"unpriced:{model}", "action", f"{batch} paused: no price for {model}", why, cooldown_min=10)

    def _on_cap(self, con, batch, why):
        if self._rc:                                       # no batch of ours to pause: the client pauses its own on the 402
            notify.send(con, f"cap:{batch}", "warn", f"remote client {self._rc['client']} refused: cap reached", why,
                        cooldown_min=60)
            return
        try:
            batches.set_status(con, batch, "paused", f"spend: {why}", system=True)
        except ReinsError:
            pass
        fix = (f"reins ctl resume {batch} once the provider's window has room (or after reins config set providers...)"
               if why.startswith("provider ") and "reins config set providers." in why
               else f"reins batch approve {batch} --cap USD  then  reins ctl resume {batch}")
        notify.send(con, f"cap:{batch}", "action", f"{batch} paused: spend cap or balance reached", f"{why}\n{fix}",
                    cooldown_min=10)


class Server(ThreadingHTTPServer):
    request_queue_size = 128     # default 5: 40 judge workers connecting at once got "connection reset by peer"
    daemon_threads = True


def serve(port: int | None = None) -> None:
    for spec in providers.all_().values():
        k = providers.key(spec)
        if k:
            KEYS[spec["name"]] = k
    if not KEYS:
        raise ReinsError(f"no provider key found: write one to {home() / 'secrets'}/<provider>.key (chmod 600)")
    port = port or config()["gateway_port"]
    (home() / "cache").mkdir(parents=True, exist_ok=True)
    from .store import enable_stack_dumps
    enable_stack_dumps()                                  # kill -USR1: every thread's stack into the journal
    global _ACTIVE, MAX_ACTIVE
    MAX_ACTIVE = int(config().get("gateway_max_active", MAX_ACTIVE))
    _ACTIVE = threading.BoundedSemaphore(MAX_ACTIVE)
    connect().close()                                     # schema and migrations once; requests skip them
    host = str(config().get("gateway_host") or "127.0.0.1")   # 0.0.0.0 / a LAN address: shared with remote clients
    srv = Server((host, port), Handler)
    print(f"reins gateway on http://{host}:{port}/api/v1  (cache {home() / 'cache'})", file=sys.stderr)
    srv.serve_forever()
