"""A shared gateway: one machine holds the provider keys, other machines (colleagues) run their own reins and send
their paid calls to it.

    on the machine with the keys (gateway_host = "0.0.0.0" in its config.toml, then restart the gateway):
        reins gateway client add NAME --daily-cap USD --weekly-cap USD --why '...'
            -> secrets/clients/NAME.token (hand that file to the colleague; never by chat or git)
        reins gateway client list | remove NAME --why '...'
    on the colleague's machine (its own REINS_HOME, no provider keys):
        reins gateway join http://HOST:8790 --client NAME --token-file FILE --why '...'

The colleague's gateway keeps everything local -- batches, caps, the cache, the spend ledger, the sentinel -- and
forwards each uncached call to the shared gateway with  rc|<client>|<secret>|<batch>|<stage>|<flags>  instead of a
key. The shared gateway checks the token, holds the cost against that client's own caps ([remote_clients.NAME]) and
the providers' caps, answers from its cache when it can (shared by every machine), books the call under
remote:<client>:<batch>, and returns what it booked in X-Reins-Cost (0 on its cache)."""
from __future__ import annotations

import os
import secrets as _secrets

from . import gateway, settings_edit, spend
from .store import ReinsError, config, home

CAPS = ("hourly_cap", "daily_cap", "weekly_cap")


def add_client(con, name: str, caps: dict, why: str, by: str = "user") -> dict:
    if not gateway.CLIENT_NAME.fullmatch(name):
        raise ReinsError(f"client name {name!r}: lower-case letters, digits and '-' (at most 40)")
    caps = {k: v for k, v in caps.items() if v is not None}
    if not caps:
        raise ReinsError("a remote client needs at least one cap: --daily-cap / --weekly-cap / --hourly-cap USD")
    for k, v in caps.items():
        settings_edit.set_(con, f"remote_clients.{name}.{k}", repr(float(v)), why, by, new_key=True)
    f = gateway.client_token_path(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    made = not f.is_file()
    if made:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(_secrets.token_urlsafe(32) + "\n")
    return {"client": name, "token_file": str(f), "new_token": made, "caps": caps}


def remove_client(con, name: str, why: str, by: str = "user") -> dict:
    """The token file goes (every call of that client is refused from now on); its caps are set to 0 and kept as
    history."""
    if name not in (config().get("remote_clients") or {}):
        raise ReinsError(f"no remote client {name!r}")
    for k in CAPS:
        if (config()["remote_clients"][name] or {}).get(k) is not None:
            settings_edit.set_(con, f"remote_clients.{name}.{k}", "0.0", why, by)
    f = gateway.client_token_path(name)
    if f.is_file():
        f.unlink()
    return {"client": name, "removed": True}


def clients(con) -> list[dict]:
    out = []
    for name, sec in sorted((config().get("remote_clients") or {}).items()):
        if not isinstance(sec, dict):
            continue
        u = spend.client_usage(con, name)
        n = con.execute("SELECT COUNT(*), COALESCE(SUM(cache_hit),0), MAX(at) FROM spend WHERE batch_id LIKE ?",
                        (spend.remote_id(name, "%"),)).fetchone()
        out.append({"client": name, "token": gateway.client_token_path(name).is_file(),
                    "caps": {k: sec.get(k) for k in CAPS if sec.get(k) is not None},
                    "spent": {k: round(v, 3) for k, v in u.items()}, "calls": n[0], "cache_hits": n[1], "last": n[2]})
    return out


def join(con, url: str, client: str, token_file, why: str, by: str = "user") -> dict:
    """This machine sends its paid calls to the shared gateway at `url` as `client`."""
    import urllib.request
    if not gateway.CLIENT_NAME.fullmatch(client):
        raise ReinsError(f"client name {client!r}: lower-case letters, digits and '-'")
    tok = open(token_file).read().strip()
    if not tok:
        raise ReinsError(f"{token_file} is empty")
    url = url.rstrip("/")
    try:
        urllib.request.urlopen(url + "/health", timeout=5)
    except Exception as e:                                                       # noqa: BLE001
        raise ReinsError(f"the shared gateway does not answer at {url}/health: {e}")
    f = home() / "secrets" / "remote.token"
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(tok + "\n")
    os.chmod(f, 0o600)
    for k, v in (("remote.gateway", url), ("remote.client", client)):
        settings_edit.set_(con, k, f'"{v}"', why, by, new_key=True)
    return {"gateway": url, "client": client, "token_file": str(f)}
