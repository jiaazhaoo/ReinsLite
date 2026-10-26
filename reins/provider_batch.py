"""Provider batch mode (C9): many chat requests sent as ONE provider batch job, at the provider's batch price.

    POST /p/<provider>/v1/batch          {"model": M, "requests": [<chat body>, ...]}
         -> {"job": ID, "cached": {"<index>": <chat answer>}, "submitted": N}
    GET  /p/<provider>/v1/batch/<ID>     -> {"state": "pending" | "done" | "failed", "answers": {"<index>": <chat answer>}}

Both carry the usual `Authorization: Bearer <batch_id>[:<stage>]`. A request already in the gateway cache comes back at
once (free) and is not submitted; the rest is reserved at the estimate x the provider's `batch_discount`, settled when
the job is collected, and every answer is cached under its own request (so a later run, batch or not, gets it free).
Answers come back in the chat-completions shape the client already parses.

A provider supports this when its spec names a `batch` adapter (reins/providers.py); today one: "gemini_native" --
Google's generativelanguage batchGenerateContent with inline requests (the job's whole body must stay under ~20 MB;
the client splits). Jobs live in $REINS_HOME/batch_jobs/<ID>.json until collected.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

from .store import home

DONE = ("JOB_STATE_SUCCEEDED", "BATCH_STATE_SUCCEEDED")
FAILED = ("JOB_STATE_FAILED", "BATCH_STATE_FAILED", "JOB_STATE_CANCELLED", "BATCH_STATE_CANCELLED",
          "JOB_STATE_EXPIRED", "BATCH_STATE_EXPIRED")


def jobs_dir():
    d = home() / "batch_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ gemini_native: chat body <-> Gemini native
def native_base(spec: dict) -> str:
    return spec.get("batch_base") or spec["base"].rstrip("/").removesuffix("/openai")


def to_native(body: dict) -> dict:
    """One OpenAI-style chat body -> a GenerateContentRequest (text + inline base64 images, JSON output, temperature)."""
    contents = []
    for m in body.get("messages", []):
        parts = []
        c = m.get("content")
        for p in ([{"type": "text", "text": c}] if isinstance(c, str) else c or []):
            if p.get("type") == "text":
                parts.append({"text": p["text"]})
            elif p.get("type") == "image_url":
                url = p["image_url"]["url"]
                head, _, data = url.partition(",")
                mime = head.removeprefix("data:").split(";")[0] or "image/jpeg"
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
        contents.append({"role": "model" if m.get("role") == "assistant" else "user", "parts": parts})
    gen = {}
    if "temperature" in body:
        gen["temperature"] = body["temperature"]
    if body.get("max_tokens"):
        gen["maxOutputTokens"] = body["max_tokens"]
    if (body.get("response_format") or {}).get("type") == "json_object":
        gen["responseMimeType"] = "application/json"
    if body.get("media_resolution"):                  # e.g. MEDIA_RESOLUTION_HIGH: image detail (native default may be lower)
        gen["mediaResolution"] = body["media_resolution"]
    if body.get("reasoning_effort"):                  # OpenAI-style effort -> Gemini thinking level (low | high)
        gen["thinkingConfig"] = {"thinkingLevel": body["reasoning_effort"]}
    return {"contents": contents, "generationConfig": gen}


def from_native(resp: dict, model: str) -> dict:
    """A GenerateContentResponse -> a chat-completions answer; thinking tokens are counted as output (they are billed)."""
    cand = (resp.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    u = resp.get("usageMetadata") or {}
    pin = u.get("promptTokenCount", 0)
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return {"object": "chat.completion", "model": model, "batch": True,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": (cand.get("finishReason") or "").lower()}],
            "usage": {"prompt_tokens": pin, "completion_tokens": out, "total_tokens": pin + out}}


def _call(method: str, url: str, key: str, payload: dict | None = None, timeout: float = 300) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def submit(spec: dict, key: str, model: str, items: list[tuple[str, dict]]) -> tuple[int, dict]:
    """items = [(request sha, chat body)] -> (HTTP status, provider answer: the batch operation)."""
    reqs = [{"request": to_native(b), "metadata": {"key": sha}} for sha, b in items]
    payload = {"batch": {"display_name": f"reins-{uuid.uuid4().hex[:8]}",
                         "input_config": {"requests": {"requests": reqs}}}}
    code, raw = _call("POST", f"{native_base(spec)}/models/{model}:batchGenerateContent", key, payload)
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, {"error": raw[:500].decode("utf-8", "replace")}


def poll(spec: dict, key: str, name: str) -> tuple[str, dict[str, dict] | None, str]:
    """-> (pending | done | failed, {request sha: native response or {"error": ...}} when done, provider state)."""
    code, raw = _call("GET", f"{native_base(spec)}/{name}", key, timeout=120)
    if code != 200:
        return "pending", None, f"HTTP {code}"
    op = json.loads(raw)
    meta = op.get("metadata") or {}
    state = meta.get("state") or (op.get("response") or {}).get("state") or ""
    if op.get("error") or state in FAILED:
        return "failed", None, state or json.dumps(op.get("error"))[:200]
    if not op.get("done") and state not in DONE:
        return "pending", None, state
    out = (op.get("response") or {}).get("inlinedResponses") or (meta.get("output") or {}).get("inlinedResponses") or {}
    rows = out.get("inlinedResponses", out) if isinstance(out, dict) else out
    answers = {}
    for r in rows or []:
        sha = (r.get("metadata") or {}).get("key")
        if sha:
            answers[sha] = r.get("response") or {"error": r.get("error") or "no response"}
    return "done", answers, state


ADAPTERS = {"gemini_native": (submit, poll)}


def new_job(record: dict) -> str:
    jid = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    (jobs_dir() / f"{jid}.json").write_text(json.dumps(record))
    return jid


def load_job(jid: str) -> dict | None:
    p = jobs_dir() / f"{jid}.json"
    return json.loads(p.read_text()) if p.is_file() and "/" not in jid else None


def save_job(jid: str, record: dict) -> None:
    (jobs_dir() / f"{jid}.json").write_text(json.dumps(record))
