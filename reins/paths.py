"""Paths the registry recorded before a folder was moved. The registry keeps history as it was (a batch's work_dir is
where it ran); whatever reads files goes through resolve(), which replays the machine's move logs to find where the
path is now.

    config.toml:   [paths]
                   moves = ["/data/system/_layout/moves.tsv"]     # tab-separated, a header with `old` and `new`, oldest first
    reins paths resolve PATH          where a recorded path is now
    reins paths check                 recorded paths of live batches, weights and services that cannot be found any more

2026-10-22: /data was reorganised (858 moves); 103 of 120 batch work dirs no longer existed under their recorded names
and the production page showed 0 results for every work package.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

from .store import config

_CACHE: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def _moves() -> list[tuple[str, str]]:
    out = []
    for f in (config().get("paths") or {}).get("moves", []):
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if f not in _CACHE or _CACHE[f][0] != mt:
            rows = []
            with open(f, newline="", encoding="utf-8", errors="replace") as fh:
                for r in csv.DictReader(fh, delimiter="\t"):
                    old, new = (r.get("old") or "").rstrip("/"), (r.get("new") or "").rstrip("/")
                    if not old.startswith("/") or not new:
                        continue
                    if not new.startswith("/"):                  # a link-style target: relative to the old path's folder
                        new = os.path.normpath(os.path.join(os.path.dirname(old), new))
                    rows.append((old, new))
            _CACHE[f] = (mt, rows)
        out += _CACHE[f][1]
    return out


def resolve(p: str | os.PathLike | None) -> str | None:
    """Where a recorded path is now: itself if it exists, else the path after replaying every move (oldest first);
    the original when that does not exist either."""
    if not p:
        return p
    s = str(p).rstrip("/") or "/"
    if os.path.exists(s):
        return str(p)
    cur = s
    for old, new in _moves():
        if cur == old or cur.startswith(old + "/"):
            cur = new + cur[len(old):]
    return cur if cur != s and os.path.exists(cur) else str(p)


def work_dir(b) -> str | None:
    """A batch's work dir where it is now (the registry keeps where it was)."""
    return resolve(b["work_dir"]) if b["work_dir"] else None


def check(con) -> list[dict]:
    """Recorded paths still in use that resolve() cannot find: live batches' work dirs, weights, live services' data."""
    import json
    out = []
    for r in con.execute("SELECT batch_id, work_dir FROM batch WHERE status IN ('open','running','paused') AND work_dir IS NOT NULL"):
        if not os.path.exists(resolve(r["work_dir"])):
            out.append({"what": f"batch {r['batch_id']}", "path": r["work_dir"]})
    for r in con.execute("SELECT name, json_extract(body,'$.path') p FROM artifact WHERE kind='weights' AND status<>'retired'"):
        if r["p"] and not os.path.exists(resolve(r["p"])):
            out.append({"what": r["name"], "path": r["p"]})
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='service'").fetchone():
        for r in con.execute("SELECT name, data FROM service WHERE status='live'"):
            for d in json.loads(r["data"] or "[]"):
                if not os.path.exists(resolve(d)):
                    out.append({"what": f"service {r['name']}", "path": d})
    return out
