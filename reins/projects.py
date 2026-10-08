"""The projects this machine manages: every checkout directly under the code root with a reins.toml (linked worktrees
are checkouts of a project, not projects). Read by the hook (what a session may run) and the session index."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .store import CODE_ROOT

_CACHE: dict[Path, list[tuple[Path, dict]]] = {}


def root() -> Path:
    return Path(os.environ.get("REINS_CODE_ROOT") or CODE_ROOT)


def all_() -> list[tuple[Path, dict]]:
    r = root()
    if r not in _CACHE:
        found = []
        for f in sorted(r.glob("*/reins.toml")):
            if not (f.parent / ".git").is_dir():
                continue
            try:
                found.append((f.parent, tomllib.loads(f.read_text(encoding="utf-8"))))
            except (OSError, tomllib.TOMLDecodeError):
                pass
        _CACHE[r] = found
    return _CACHE[r]


def run_programs() -> tuple[set[str], set[str]]:
    """(script names, python module names) that only `reins run` may start: every project's [run] programs."""
    scripts, mods = set(), set()
    for _, cfg in all_():
        for p in (cfg.get("run") or {}).get("programs", []):
            (scripts if p.endswith((".sh", ".py")) or "." not in p else mods).add(p)
    return scripts, mods


SECTIONS = {"prompts": "prompts", "models": "models", "weights": "weights", "tools": "tools"}


def check_config(cfg: dict) -> list[str]:
    """Problems in a project's reins.toml that would only show up later: names used but never declared, stages that
    name an unknown module, a case pattern that does not compile, scope parts that are not tokens."""
    import re
    out = []
    if not cfg.get("project"):
        out.append("project = \"...\" is missing")
    try:
        re.compile(cfg.get("case_id_pattern", ".+"))
    except re.error as e:
        out.append(f"case_id_pattern does not compile: {e}")
    for p in (cfg.get("batch") or {}).get("scope", []):
        if not re.match(r"^[a-z][a-z0-9_]*$", p):
            out.append(f"[batch] scope part {p!r}: lowercase letters, digits and _")
    mods = cfg.get("modules") or {}
    for m, spec in mods.items():
        if not spec.get("about"):
            out.append(f"[modules.{m}] has no about (the board shows it)")
        for sec in SECTIONS:
            for n in spec.get(sec, []):
                if n not in (cfg.get(sec) or {}):
                    out.append(f"[modules.{m}] {sec} names {n!r}, which has no [{sec}.{n}]")
    for w, wf in (cfg.get("workflows") or {}).items():
        names = [s.get("name") for s in wf.get("stages", [])]
        if len(set(names)) != len(names):
            out.append(f"[workflows.{w}] stage names repeat: {names}")
        for st in wf.get("stages", []):
            for k in ["module"] + (["also"] if st.get("also") else []):
                for m in ([st[k]] if isinstance(st.get(k), str) else st.get(k) or []):
                    if m not in mods:
                        out.append(f"[workflows.{w}] stage {st.get('name')!r} names module {m!r}, which has no [modules.{m}]")
            for sec in SECTIONS:
                for n in st.get(sec, []):
                    if n not in (cfg.get(sec) or {}):
                        out.append(f"[workflows.{w}] stage {st.get('name')!r} {sec} names {n!r}, which has no [{sec}.{n}]")
    run_stages = (cfg.get("run") or {}).get("stages")
    for w, wf in (cfg.get("workflows") or {}).items():
        if run_stages and [s.get("name") for s in wf.get("stages", [])] != run_stages:
            out.append(f"[run] stages {run_stages} differ from [workflows.{w}] stages")
    return out
