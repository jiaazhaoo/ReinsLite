"""The projects this machine manages, and where each one's settings live.

A project's settings (reins.toml, and beside it rules.toml / review.toml) live either
  - outside the repo, in ReinsLite: adapters/<name>/reins.toml with `repo = "/path/of/the/checkout"` (the managed code
    then knows nothing of reins; the adapter folder also holds the glue that drives it), or
  - inside the repo, as <repo>/reins.toml (older projects, and release trees cut before their settings moved out).
The adapter wins for its repo. Every reader goes through config_dir() / load() / find_root(), never `repo / "reins.toml"`.

all_() lists every managed project: adapters whose repo exists, plus every checkout directly under the code root with a
reins.toml (linked worktrees are checkouts of a project, not projects). Read by the hook (what a session may run) and
the session index."""
from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

from .store import CODE_ROOT

_CACHE: dict[tuple, list[tuple[Path, dict]]] = {}
_EXT: dict[Path, dict[Path, Path]] = {}


def root() -> Path:
    return Path(os.environ.get("REINS_CODE_ROOT") or CODE_ROOT)


def adapters_dir() -> Path:
    return Path(os.environ.get("REINS_ADAPTERS") or Path(__file__).resolve().parents[1] / "adapters")


def _repo_of_cfg(cfg: dict) -> str | None:
    return cfg.get("repo") or (cfg.get("dev") or {}).get("repo")


def external() -> dict[Path, Path]:
    """{managed repo: adapter folder holding its reins.toml}."""
    a = adapters_dir()
    if a not in _EXT:
        out = {}
        for f in sorted(a.glob("*/reins.toml")) if a.is_dir() else []:
            try:
                r = _repo_of_cfg(tomllib.loads(f.read_text(encoding="utf-8")))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if r:
                out[Path(r).resolve()] = f.parent
        _EXT[a] = out
    return _EXT[a]


def main_checkout(d: Path) -> Path:
    """The repository a checkout belongs to: a linked worktree (candidate, release tree) maps back to its main repo."""
    g = d / ".git"
    if g.is_file():
        try:
            gd = g.read_text().split(":", 1)[1].strip()
            return Path(gd).parents[2] if "/worktrees/" in gd else d
        except (OSError, IndexError):
            return d
    return d


def config_dir(repo: Path | None) -> Path | None:
    """The folder holding this repo's reins.toml (an adapter folder, else the repo itself), or None if unmanaged."""
    if not repo:
        return None
    ext = external().get(Path(repo).resolve())
    if ext:
        return ext
    return Path(repo) if (Path(repo) / "reins.toml").is_file() else None


def config_file(repo: Path | None) -> Path | None:
    d = config_dir(repo)
    return d / "reins.toml" if d else None


def side_file(repo: Path | None, name: str) -> Path:
    """rules.toml / review.toml of a project: beside its reins.toml (the repo itself when it has none)."""
    return (config_dir(repo) or Path(repo or ".")) / name


def load(repo: Path | None) -> dict:
    f = config_file(repo)
    if not f:
        return {}
    try:
        return tomllib.loads(f.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def managed(repo: Path | None) -> bool:
    return config_file(repo) is not None


def find_root(cwd: Path) -> Path | None:
    """The project's main checkout for a directory: the nearest managed checkout above it (an adapter's repo, or a
    folder with its own reins.toml), mapped from a linked worktree back to the repository it belongs to."""
    ext = external()
    for d in (cwd, *cwd.parents):
        rd = d.resolve()
        if rd in ext:
            return rd
        if (d / ".git").exists():
            m = main_checkout(d).resolve()
            if m in ext:
                return m
        if (d / "reins.toml").is_file():
            r = subprocess.run(["git", "-C", str(d), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                               capture_output=True, text=True)
            common = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
            if common and common.name == ".git" and (common.parent / "reins.toml").is_file():
                return common.parent
            return d
    return None


def all_() -> list[tuple[Path, dict]]:
    r = root()
    key = (r, adapters_dir())
    if key not in _CACHE:
        found, seen = [], set()
        for repo, ad in external().items():
            if repo.is_dir():
                try:
                    found.append((repo, tomllib.loads((ad / "reins.toml").read_text(encoding="utf-8"))))
                    seen.add(repo)
                except (OSError, tomllib.TOMLDecodeError):
                    pass
        for f in sorted(r.glob("*/reins.toml")):
            if not (f.parent / ".git").is_dir() or f.parent.resolve() in seen:
                continue
            try:
                found.append((f.parent, tomllib.loads(f.read_text(encoding="utf-8"))))
            except (OSError, tomllib.TOMLDecodeError):
                pass
        _CACHE[key] = found
    return _CACHE[key]


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
    from . import resources
    out += resources.check_rules(cfg)
    run_stages = (cfg.get("run") or {}).get("stages")
    for w, wf in (cfg.get("workflows") or {}).items():
        if run_stages and [s.get("name") for s in wf.get("stages", [])] != run_stages:
            out.append(f"[run] stages {run_stages} differ from [workflows.{w}] stages")
    return out
