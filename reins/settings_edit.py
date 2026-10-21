"""Changes to $REINS_HOME/config.toml (spend caps, thresholds, where backups go) recorded like any other decision.

    reins config set pool.daily_cap 30 --why 'Mansfield rework needs two judge passes today'
    reins config show [KEY]
    reins config history

The file keeps its comments: only the value of that key changes (or the key is added to its section). Before each
change the old file is kept in $REINS_HOME/config-history/; the change is a decision ("config: pool.daily_cap 20 -> 30")
with the reason as its evidence. Until now caps were edited by hand and eight .bak copies were the only trace.
"""
from __future__ import annotations

import re
import shutil
import tomllib

from . import decisions
from .store import ReinsError, config, home, now


def _path():
    return home() / "config.toml"


def _literal(raw: str):
    """The TOML value a command-line word means: 30, 0.5, true, "text", [..]."""
    try:
        return tomllib.loads(f"v = {raw}")["v"], raw
    except tomllib.TOMLDecodeError:
        text = raw.replace("\\", "\\\\").replace('"', '\\"')
        return raw, f'"{text}"'


def get(key: str):
    cur = config()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_(con, key: str, raw: str, why: str, by: str = "user", new_key: bool = False) -> dict:
    if not why.strip():
        raise ReinsError("a config change needs --why: one line saying why")
    if not re.fullmatch(r"[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*)?", key):
        raise ReinsError(f"key {key!r}: name or section.name")
    section, name = key.split(".", 1) if "." in key else (None, key)
    value, toml = _literal(raw)
    old = get(key)
    cur = config()
    if section is None and isinstance(cur.get(name), dict):
        raise ReinsError(f"{key} is a section; set one of its values: " + ", ".join(f"{key}.{k}" for k in cur[name]))
    if old is None and not new_key:
        known = sorted(f"{k}.{kk}" for k, v in cur.items() if isinstance(v, dict) for kk in v) + sorted(k for k, v in cur.items() if not isinstance(v, dict))
        close = [k for k in known if name in k][:6]
        raise ReinsError(f"{key} is not a known setting" + (f" (did you mean {', '.join(close)}?)" if close else "")
                         + "; add a new one with --new")
    if old is not None and isinstance(old, (int, float)) != isinstance(value, (int, float)):
        raise ReinsError(f"{key} is {type(old).__name__} ({old!r}); {raw!r} is not")
    p = _path()
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    lines = text.splitlines()
    # the lines of the section (top level: before the first header)
    start, end = 0, len(lines)
    if section:
        hdr = next((i for i, l in enumerate(lines) if re.fullmatch(rf"\s*\[{re.escape(section)}\]\s*(#.*)?", l)), None)
        if hdr is None:
            lines += ["", f"[{section}]"]
            hdr = len(lines) - 1
        start = hdr + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"\s*\[", lines[i])), len(lines))
    pat = re.compile(rf"^(\s*{re.escape(name)}\s*=\s*)([^#]*?)(\s*#.*)?$")
    at = next((i for i in range(start, end) if pat.match(lines[i])), None)
    if at is not None:
        m = pat.match(lines[at])
        lines[at] = f"{m.group(1)}{toml}{m.group(3) or ''}"
    else:
        ins = end
        while ins > start and not lines[ins - 1].strip():
            ins -= 1
        lines.insert(ins, f"{name} = {toml}")
    new_text = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ReinsError(f"the change would break {p}: {e}")
    got = parsed.get(section, {}).get(name) if section else parsed.get(name)
    if got != value:
        raise ReinsError(f"{key} would read {got!r}, not {value!r}; edit {p} by hand")
    hist = home() / "config-history"
    hist.mkdir(parents=True, exist_ok=True)
    kept = None
    if p.exists():
        kept = hist / f"config-{now().replace(':', '')}.toml"
        shutil.copy2(p, kept)
    tmp = p.with_suffix(".toml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(p)
    did = decisions.add(con, f"config: {key} {old!r} -> {value!r}", why.strip(), by)
    return {"key": key, "old": old, "new": value, "decision": did, "kept": str(kept) if kept else None,
            "usage": usage(con) if section == "pool" else None}


def usage(con) -> str:
    """What the pool limits are measured against: actual spend per day, so a raise is decided on numbers."""
    import datetime as _dt
    since = (_dt.date.today() - _dt.timedelta(days=30)).isoformat()
    days = [r[0] for r in con.execute("SELECT SUM(amount) FROM spend WHERE at>=? GROUP BY substr(at,1,10)", (since,))]
    from . import spend
    c = spend.commitment(con)
    if not days:
        return "no spend in the last 30 days"
    days.sort()
    return (f"last 30 days: {len(days)} days with spend, highest ${days[-1]:.2f}/day, median ${days[len(days) // 2]:.2f}; "
            f"today ${c['today']:.2f}; live batches may still spend ${c['held_total']:.2f}")


def history(con) -> list[dict]:
    return [d for d in decisions.list_(con, all_=True) if d["text"].startswith("config: ")]
