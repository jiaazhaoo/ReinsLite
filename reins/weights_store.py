"""Registered weights kept somewhere other than this machine, and fetched back on a new one.

A project names its store in reins.toml; the weights it trained are uploaded there, one folder per model (named for
what the model does), one sub-folder per registered version, each with a manifest of sha256s. A new machine needs only
the store and this module, not the registry: `pull` reads the store's own index and manifests and refuses a file whose
bytes differ.

    [weights_store]
    kind = "hf"                       # a Hugging Face model repo (private), through the `hf` CLI the user logged in
    repo = "owner/name"
    prefix = "my-pipeline"            # folder inside the repo (one repo can hold several projects)
    upload = ["trained"]              # which [weights.X] origins go up (default: trained; pretrained ones are public)
    # kind = "dir", path = "/mnt/backup/weights"   -- a mounted disk; same layout
    [weights_store.folders]           # weights name -> folder name a person understands (default: the name)
    page_classifier = "page-text-or-drawing"

    reins weights push [--repo R]                          upload what is not up yet (watchdog: every 6 h)
    reins weights push --name weights-x-v1 ...             also registered weights the project does not declare
    reins weights pull --to DIR [--name weights-x-v2 ...]  fetch the pipeline's current versions (or the named ones)

Layout in the store:  <prefix>/README.md                     one line per model folder: what it does, current version
                      <prefix>/index.json                    {folder: {base, current, versions, in_pipeline}}
                      <prefix>/<folder>/README.md            the model's versions: date, size, metric, note; current one
                      <prefix>/<folder>/vN/manifest.json     {files: {rel: {sha256, size}}, about, training}
                      <prefix>/<folder>/vN/<rel>             the files
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import artifacts, notify
from .store import ReinsError, home, now, session, tx

EVENT = "uploaded"
METRICS = ("metrics/mAP50(M)", "metrics/mAP50(B)", "accuracy", "f1", "f1_text", "val_acc")


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, spec: dict):
        self.kind = spec.get("kind")
        self.prefix = (spec.get("prefix") or "").strip("/")
        self.upload_origins = spec.get("upload") or ["trained"]
        self.folders = dict(spec.get("folders") or {})
        if self.kind == "hf":
            if not spec.get("repo"):
                raise ReinsError("[weights_store] kind = 'hf' needs repo = 'owner/name'")
            self.repo = spec["repo"]
            self.hf = os.environ.get("REINS_HF_CMD") or shutil.which("hf") or str(Path.home() / ".local/bin/hf")
            self.label = f"hf:{self.repo}"
        elif self.kind == "dir":
            self.root = Path(spec["path"])
            self.label = f"dir:{self.root}"
        else:
            raise ReinsError(f"[weights_store] kind {self.kind!r}: use 'hf' or 'dir'")

    def key(self, *parts: str) -> str:
        return "/".join(p for p in (self.prefix, *parts) if p)

    def folder(self, base: str) -> str:
        return self.folders.get(base, base)

    def vkey(self, base: str, version: int, *rel: str) -> str:
        return self.key(self.folder(base), f"v{version}", *rel)

    def put(self, local: Path, key: str, message: str) -> str:
        if self.kind == "dir":
            dst = self.root / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, dst)
            return "copied"
        r = subprocess.run([self.hf, "upload", self.repo, str(local), key, "--commit-message", message],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode:
            raise ReinsError(f"hf upload {key}: {(r.stderr or r.stdout).strip()[-300:]}")
        return (r.stdout.strip().splitlines() or [""])[-1]                       # the commit URL

    def get(self, key: str, dest_dir: Path) -> Path | None:
        """The file at `key`, saved under dest_dir/key; None when the store has no such file."""
        out = dest_dir / key
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.kind == "dir":
            src = self.root / key
            if not src.is_file():
                return None
            shutil.copyfile(src, out)
            return out
        r = subprocess.run([self.hf, "download", self.repo, key, "--local-dir", str(dest_dir)],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode:
            if any(m in r.stderr for m in ("404", "EntryNotFound", "does not exist", "File not found")):
                return None
            raise ReinsError(f"hf download {key}: {r.stderr.strip()[-300:]}")
        return out if out.is_file() else None

    def delete(self, pattern: str, message: str) -> None:
        """Remove the files matching `pattern` (a glob under the store root; '*' crosses folders)."""
        if self.kind == "dir":
            for f in [p for p in self.root.rglob("*") if p.is_file() and fnmatch.fnmatch(str(p.relative_to(self.root)), pattern)]:
                f.unlink()
            return
        r = subprocess.run([self.hf, "repos", "delete-files", self.repo, pattern, "--commit-message", message],
                           capture_output=True, text=True, timeout=600)
        if r.returncode and "No files" not in (r.stderr + r.stdout):
            raise ReinsError(f"hf delete {pattern}: {(r.stderr or r.stdout).strip()[-300:]}")


def store_of(cfg: dict) -> Store | None:
    spec = cfg.get("weights_store")
    return Store(spec) if spec else None


def _art(con, name: str):
    a = artifacts.get(con, name)
    if a["kind"] != "weights":
        raise ReinsError(f"{name} is not a weights artifact")
    return a


def uploaded(con, name: str, store: Store) -> bool:
    """Up in this store at the place this layout puts it (a folder renamed in reins.toml means upload again)."""
    a = _art(con, name)
    want = json.dumps(store.vkey(a["base"], a["version"]))
    return con.execute("SELECT 1 FROM artifact_event WHERE name=? AND event=? AND detail LIKE ? AND detail LIKE ?",
                       (name, EVENT, f'%"store": "{store.label}"%', f'%"key": {want}%')).fetchone() is not None


def upload(con, name: str, store: Store) -> dict:
    """One registered weights version: its files (checked against the registry first), then its manifest."""
    a = _art(con, name)
    body = json.loads(a["body"])
    root = Path(body["path"])
    for rel, meta in sorted(body["files"].items()):
        f = root / rel
        if not f.is_file():
            raise ReinsError(f"{name}: {f} is gone; it cannot be uploaded")
        if _sha_file(f) != meta["sha256"]:
            raise ReinsError(f"{name}: {f} changed since it was registered; register it again (a new version)")
        store.put(f, store.vkey(a["base"], a["version"], rel), f"{store.folder(a['base'])} v{a['version']}: {rel}")
    manifest = {"name": name, "base": a["base"], "folder": store.folder(a["base"]), "version": a["version"],
                "about": a["about"], "files": body["files"], "bytes": body["bytes"], "training": body.get("training") or {},
                "registered": a["created"], "uploaded": now()}
    with tempfile.TemporaryDirectory() as d:
        mf = Path(d) / "manifest.json"
        mf.write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        ref = store.put(mf, store.vkey(a["base"], a["version"], "manifest.json"), f"{store.folder(a['base'])} v{a['version']}: manifest")
    with tx(con):
        con.execute("INSERT INTO artifact_event (at, name, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), name, EVENT, json.dumps({"store": store.label, "key": store.vkey(a["base"], a["version"]), "ref": ref}),
                     session()))
    return {"name": name, "folder": store.folder(a["base"]), "version": a["version"], "files": len(body["files"]),
            "bytes": body["bytes"]}


def _declared(cfg: dict, store: Store) -> list[str]:
    return [b for b, s in (cfg.get("weights") or {}).items() if (s.get("origin") or "trained") in store.upload_origins]


def wanted(con, cfg: dict, store: Store) -> list[str]:
    """Registered versions of the bases this project declares with an uploaded origin (retired ones too: an old
    release may still need them)."""
    bases = _declared(cfg, store)
    if not bases:
        return []
    artifacts.ensure(con)
    q = f"SELECT name FROM artifact WHERE kind='weights' AND base IN ({','.join('?' * len(bases))}) ORDER BY base, version"
    return [r["name"] for r in con.execute(q, bases)]


def _dropped(a, declared: set[str]) -> bool:
    """A retired experiment (its base not in the pipeline) leaves the store: nothing can need it."""
    return a["status"] == "retired" and a["base"] not in declared


def _in_store(con, store: Store, declared: set[str] = frozenset()) -> dict[str, list]:
    """base -> its versions up in this store (registry rows), oldest first."""
    names = {r["name"] for r in con.execute("SELECT DISTINCT name FROM artifact_event WHERE event=? AND detail LIKE ?",
                                            (EVENT, f'%"store": "{store.label}"%'))}
    out: dict[str, list] = {}
    for n in sorted(names):
        try:
            a = _art(con, n)
            if uploaded(con, n, store) and not _dropped(a, declared):
                out.setdefault(a["base"], []).append(a)
        except ReinsError:
            continue
    for v in out.values():
        v.sort(key=lambda a: a["version"])
    return out


def _metric(body: dict) -> str:
    m = (body.get("training") or {}).get("metrics") or {}
    k = next((k for k in METRICS if k in m), None)
    if not k:
        return ""
    try:
        return f"{k.replace('metrics/', '')} {float(m[k]):.3f}"
    except (TypeError, ValueError):
        return f"{k} {m[k]}"


def _current(versions: list) -> dict | None:
    act = [a for a in versions if a["status"] == "active"]
    live = [a for a in versions if a["status"] != "retired"]
    return (act or live or [None])[-1]


def _write_index(con, cfg: dict, store: Store, folders_touched: set[str] | None = None) -> None:
    """index.json, the top README and the README of each folder whose versions changed."""
    declared = set(_declared(cfg, store))
    ups = _in_store(con, store, declared)
    idx, lines = {}, []
    for base, vs in sorted(ups.items(), key=lambda kv: store.folder(kv[0])):
        cur = _current(vs)
        folder = store.folder(base)
        idx[folder] = {"base": base, "current": f"v{cur['version']}" if cur else None,
                       "versions": [f"v{a['version']}" for a in vs], "in_pipeline": base in declared}
        about = (cur or vs[-1])["about"] or ""
        lines.append(f"| [{folder}]({folder}/) | {about} | {idx[folder]['current'] or '-'} | {len(vs)} | "
                     f"{'yes' if base in declared else 'experiment'} |")
        if folders_touched is None or folder in folders_touched:
            rows = []
            for a in reversed(vs):
                b = json.loads(a["body"])
                mark = " (current)" if cur and a["name"] == cur["name"] else ""
                rows.append(f"| [v{a['version']}](v{a['version']}/){mark} | {a['status']} | {a['created'][:10]} | "
                            f"{b['bytes'] / 1e6:.0f} MB | {_metric(b)} | {a['about'] or ''} |")
            md = (f"# {folder}\n\n{about}\n\nreins name `{base}`; "
                  f"{'used by the pipeline' if base in declared else 'experiment, not in the pipeline'}. "
                  f"Each vN/ holds the files and manifest.json (sha256 of every file).\n\n"
                  "| version | status | registered | size | metric | note |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
            _put_text(store, md, store.key(folder, "README.md"), f"{folder}: README")
    top = (f"# {cfg.get('project')} model weights\n\nOne folder per model, one vN/ per version (managed by reins: "
           "`reins weights push / pull`).\n\n| model | what it does | current | versions | pipeline |\n|---|---|---|---|---|\n"
           + "\n".join(lines) + "\n")
    _put_text(store, top, store.key("README.md"), "README: models")
    _put_text(store, json.dumps({"project": cfg.get("project"), "models": idx, "at": now()}, indent=1, sort_keys=True),
              store.key("index.json"), "index: models and current versions")


def _stale_index(con, cfg: dict, store: Store) -> bool:
    """Statuses changed since the index was written (a version retired or made active): rewrite it."""
    mark = home() / f"weights_index_{hashlib.sha1((store.label + str(cfg.get('project'))).encode()).hexdigest()[:10]}.json"
    declared = set(_declared(cfg, store))
    now_ = json.dumps({b: [(a["version"], a["status"]) for a in vs] for b, vs in _in_store(con, store, declared).items()},
                      sort_keys=True)
    old = mark.read_text() if mark.exists() else None
    mark.write_text(now_)
    return old != now_


def _put_text(store: Store, text: str, key: str, message: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / Path(key).name
        f.write_text(text, encoding="utf-8")
        store.put(f, key, message)


def push(con, cfg: dict, notify_fail: bool = True, names: list[str] | None = None) -> dict:
    """The project's declared trained weights, or `names`: any registered weights (an experiment not in the pipeline
    yet is still only on this machine)."""
    store = store_of(cfg)
    if not store:
        return {"store": None, "skipped": "no [weights_store] in reins.toml"}
    todo = list(names or wanted(con, cfg, store))
    if not names:                                      # anything up under an older layout moves to the current one
        declared = set(_declared(cfg, store))
        todo += [n for n in (r["name"] for r in con.execute(
            "SELECT DISTINCT name FROM artifact_event WHERE event=? AND detail LIKE ?", (EVENT, f'%"store": "{store.label}"%')))
            if n not in todo and not _dropped(_art(con, n), declared)]
    done, failed = [], []
    for name in todo:
        try:
            if uploaded(con, name, store):
                continue
            done.append(upload(con, name, store))
        except (ReinsError, subprocess.SubprocessError, OSError) as e:
            failed.append(f"{name}: {e}")
    if done or names is None and _stale_index(con, cfg, store):
        try:
            _write_index(con, cfg, store, {d["folder"] for d in done} or None)
            if not failed:
                store.delete(store.key("weights-*"), "remove the old one-folder-per-version layout")
        except (ReinsError, subprocess.SubprocessError, OSError) as e:
            failed.append(f"index / README: {e}")
    if failed and notify_fail:
        notify.send(con, f"weights_push:{cfg.get('project')}", "action", f"{cfg.get('project')}: {len(failed)} weights not uploaded",
                    "\n".join(failed[:10]) + f"\nonly copy is on this machine; fix it, then: reins weights push", cooldown_min=360)
    return {"store": store.label, "uploaded": done, "failed": failed}


def pull(cfg_or_store, dest: Path, names: list[str] | None = None) -> list[dict]:
    """Fetch versions into dest/<folder>/..., each file verified against its manifest. Works with no registry: the
    store's index names the pipeline's current versions; `names` are weights-<base>-vN."""
    store = cfg_or_store if isinstance(cfg_or_store, Store) else store_of(cfg_or_store)
    if not store:
        raise ReinsError("no [weights_store] in reins.toml")
    dest = Path(dest)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        f = store.get(store.key("index.json"), tmp)
        if not f:
            raise ReinsError(f"{store.label} has no {store.key('index.json')}: nothing pushed yet")
        models = json.loads(f.read_text())["models"]
        by_base = {m["base"]: folder for folder, m in models.items()}
        if names:
            todo = []
            for n in names:
                _, base, v = artifacts.parse(n)
                if base not in by_base:
                    raise ReinsError(f"{store.label} has no model {base}")
                todo.append((by_base[base], f"v{v}"))
        else:
            todo = [(folder, m["current"]) for folder, m in sorted(models.items()) if m["in_pipeline"] and m["current"]]
        out = []
        for folder, v in todo:
            mf = store.get(store.key(folder, v, "manifest.json"), tmp)
            if not mf:
                raise ReinsError(f"{store.label} has no {folder}/{v}")
            man = json.loads(mf.read_text())
            target = dest / folder
            for rel, meta in sorted(man["files"].items()):
                got = store.get(store.key(folder, v, rel), tmp)
                if not got or _sha_file(got) != meta["sha256"]:
                    raise ReinsError(f"{folder}/{v}/{rel}: missing or its sha256 differs from the manifest; not installed")
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(got), target / rel)
            (target / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            out.append({"name": man["name"], "dir": str(target), "files": len(man["files"]), "bytes": man["bytes"]})
    return out


def push_all(con, every_h: float = 6) -> list[str]:
    """Watchdog pass: every project with a [weights_store], at most every `every_h` hours."""
    from . import projects
    mark = home() / "last_weights_push"
    if mark.exists() and time.time() - mark.stat().st_mtime < every_h * 3600:
        return []
    mark.touch()
    out = []
    for _, cfg in projects.all_():
        if not cfg.get("weights_store"):
            continue
        try:
            r = push(con, cfg)
        except ReinsError as e:
            r = {"uploaded": [], "failed": [str(e)]}
        if r["uploaded"] or r["failed"]:
            out.append(f"weights {cfg.get('project')}: {len(r['uploaded'])} uploaded, {len(r['failed'])} failed")
    return out
