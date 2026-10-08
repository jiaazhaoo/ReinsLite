"""Metered services the gateway fronts (C9). Presets for common ones; an installation adds, overrides or disables them in
$REINS_HOME/config.toml, so nothing about a particular provider is wired into the gateway, the runner or the board.

    [providers.openrouter]                      # a preset: it only needs secrets/openrouter.key
    [providers.deepseek]
    enabled = false                             # not used here
    [providers.mistral]                         # a new OpenAI-compatible chat API
    kind = "chat"
    base = "https://api.mistral.ai/v1"
    env = "MISTRAL"                             # the runner sets MISTRAL_BASE_URL / MISTRAL_API_KEY for the pipeline
    [providers.google_maps]
    per_call = { geocode = 0.005 }              # USD per call by API path (GET services are priced per call)

Fields:
  kind         chat (POST <base>/chat/completions, OpenAI-compatible) | get (GET passthrough, key as a query parameter)
  base         upstream base URL
  env          prefix of the two variables the runner gives a pipeline (<ENV>_BASE_URL, <ENV>_API_KEY = the batch token)
  key_file     secrets/<key_file>.key (default: the provider name); <ENV>_API_KEY in the gateway's environment also works
  ledger       name in the spend ledger and on the board (default: the provider name)
  extra_body   merged into every forwarded chat body (OpenRouter: usage.include so it reports the charged cost)
  balance      {url, parse}: a free account endpoint and a built-in parser (openrouter | deepseek); absent = no balance
  low_balance  USD; the watchdog asks for a top-up below it (default 5)
  key_param, path_prefix, per_call, default_call   get services: where the key goes, which path part names the API, prices
  default_chat true for the one chat provider also served at /api/v1 (older clients)
"""
from __future__ import annotations

import os

from .store import config, home

PRESETS: dict[str, dict] = {
    "openrouter": {"kind": "chat", "base": "https://openrouter.ai/api/v1", "env": "OPENROUTER", "default_chat": True,
                   "extra_body": {"usage": {"include": True}},
                   "balance": {"url": "https://openrouter.ai/api/v1/credits", "parse": "openrouter"}},
    "deepseek": {"kind": "chat", "base": "https://api.deepseek.com/v1", "env": "DEEPSEEK",
                 "balance": {"url": "https://api.deepseek.com/user/balance", "parse": "deepseek"}},
    "openai": {"kind": "chat", "base": "https://api.openai.com/v1", "env": "OPENAI"},
    "google_maps": {"kind": "get", "base": "https://maps.googleapis.com", "env": "GOOGLE_MAPS", "key_file": "google",
                    "ledger": "google", "key_param": "key", "path_prefix": "/maps/api/", "default_call": 0.005,
                    "per_call": {"geocode": 0.005, "place/findplacefromtext": 0.017, "place/details": 0.017,
                                 "place/textsearch": 0.032, "staticmap": 0.002, "distancematrix": 0.005}},
}


def all_() -> dict[str, dict]:
    """Every provider this installation knows: presets merged with config.toml [providers.*], disabled ones dropped."""
    cfg = config().get("providers") or {}
    out = {}
    for name in list(PRESETS) + [n for n in cfg if n not in PRESETS]:
        spec = {**PRESETS.get(name, {}), **(cfg.get(name) or {})}
        if spec.get("enabled") is False or "kind" not in spec or "base" not in spec:
            continue
        spec.setdefault("env", name.upper())
        spec.setdefault("key_file", name)
        spec.setdefault("ledger", name)
        spec["name"] = name
        spec["configured"] = name in cfg
        out[name] = spec
    return out


def get(name: str) -> dict | None:
    return all_().get(name)


def by_ledger(ledger: str) -> dict | None:
    return next((p for p in all_().values() if p["ledger"] == ledger), None)


def default_chat() -> dict | None:
    ps = [p for p in all_().values() if p["kind"] == "chat"]
    return next((p for p in ps if p.get("default_chat")), ps[0] if ps else None)


def key(spec: dict) -> str:
    f = home() / "secrets" / f"{spec['key_file']}.key"
    if f.is_file():
        return f.read_text().strip()
    return os.environ.get(f"{spec['env']}_API_KEY", "")


def in_use() -> list[dict]:
    """The providers worth a card: a key is present, or the installation configured it explicitly."""
    return [p for p in all_().values() if p["configured"] or (home() / "secrets" / f"{p['key_file']}.key").is_file()]


def client_env(port: int, token: str) -> dict[str, str]:
    """What a pipeline under `reins run` gets: every provider's base URL pointing at the gateway, and the batch token as
    its key (the real keys stay in the gateway). Only providers in use: an unused preset must not redirect a client
    library that reads its default variables (OPENAI_BASE_URL ...) to a gateway without that key."""
    env = {}
    for p in in_use():
        base = f"http://127.0.0.1:{port}/p/{p['name']}" + ("/v1" if p["kind"] == "chat" else "")
        env[f"{p['env']}_BASE_URL"] = base
        env[f"{p['env']}_API_KEY"] = token
    return env


def parse_balance(parser: str, d: dict) -> tuple[float | None, float | None, str]:
    """(balance USD, usage total USD, detail) from a provider's account endpoint, by the parser it declares."""
    if parser == "openrouter":
        d = d["data"]
        return float(d["total_credits"]) - float(d["total_usage"]), float(d["total_usage"]), f"credits {d['total_credits']}"
    if parser == "deepseek":
        usd = next((b for b in d.get("balance_infos", []) if b.get("currency") == "USD"), None)
        return (float(usd["total_balance"]) if usd else None), None, \
            ", ".join(f"{b['currency']} {b['total_balance']}" for b in d.get("balance_infos", []))
    raise ValueError(f"no balance parser {parser!r}")
