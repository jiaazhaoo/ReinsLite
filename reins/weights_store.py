"""Registered weights kept somewhere other than this machine, and fetched back on a new one.

A project names its store in reins.toml; the weights it trained are uploaded there, one folder per registered version,
each with a manifest of sha256s. A new machine needs only the store and this module, not the registry: `pull` reads the
store's own index and manifests and refuses a file whose bytes differ.

    [weights_store]
    kind = "hf"                       # a Hugging Face model repo (private), through the `hf` CLI the user logged in
    repo = "owner/name"
    prefix = "my-pipeline"            # folder inside the repo (one repo can hold several projects)
    upload = ["trained"]              # which [weights.X] origins go up (default: trained; pretrained ones are public)
    # kind = "dir", path = "/mnt/backup/weights"   -- a mounted disk; same layout

    reins weights push [--repo R]                          upload what is not up yet (watchdog: every 6 h)
    reins weights pull --to DIR [--name weights-x-v2 ...]  fetch the store's active versions (or the named ones), verify

Layout in the store:  <prefix>/index.json                    {base: active version name}
                      <prefix>/<weights-base-vN>/manifest.json {files: {rel: {sha256, size}}, about, training}
                      <prefix>/<weights-base-vN>/<rel>        the files
"""
from __future__ import annotations

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


def store_of(cfg: dict) -> Store | None:
    spec = cfg.get("weights_store")
    return Store(spec) if spec else None


def uploaded(con, name: str, store: Store) -> bool:
    return con.execute("SELECT 1 FROM artifact_event WHERE name=? AND event=? AND detail LIKE ?",
                       (name, EVENT, f'%"store": "{store.label}"%')).fetchone() is not None


def upload(con, name: str, store: Store) -> dict:
    """One registered weights version: its files (checked against the registry first), then its manifest."""
    a = artifacts.get(con, name)
    if a["kind"] != "weights":
        raise ReinsError(f"{name} is not a weights artifact")
    body = json.loads(a["body"])
    root = Path(body["path"])
    for rel, meta in sorted(body["files"].items()):
        f = root / rel
        if not f.is_file():
            raise ReinsError(f"{name}: {f} is gone; it cannot be uploaded")
        if _sha_file(f) != meta["sha256"]:
            raise ReinsError(f"{name}: {f} changed since it was registered; register it again (a new version)")
        store.put(f, store.key(name, rel), f"{name}: {rel}")
    manifest = {"name": name, "base": a["base"], "version": a["version"], "about": a["about"], "files": body["files"],
                "bytes": body["bytes"], "training": body.get("training") or {}, "registered": a["created"], "uploaded": now()}
    with tempfile.TemporaryDirectory() as d:
        mf = Path(d) / "manifest.json"
        mf.write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        ref = store.put(mf, store.key(name, "manifest.json"), f"{name}: manifest")
    with tx(con):
        con.execute("INSERT INTO artifact_event (at, name, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), name, EVENT, json.dumps({"store": store.label, "key": store.key(name), "ref": ref}), session()))
    return {"name": name, "files": len(body["files"]), "bytes": body["bytes"]}


def wanted(con, cfg: dict, store: Store) -> list[str]:
    """Registered versions of the bases this project declares with an uploaded origin (retired ones too: an old
    release may still need them)."""
    bases = [b for b, s in (cfg.get("weights") or {}).items() if (s.get("origin") or "trained") in store.upload_origins]
    if not bases:
        return []
    artifacts.ensure(con)
    q = f"SELECT name FROM artifact WHERE kind='weights' AND base IN ({','.join('?' * len(bases))}) ORDER BY base, version"
    return [r["name"] for r in con.execute(q, bases)]


def _write_index(con, cfg: dict, store: Store) -> None:
    bases = [b for b, s in (cfg.get("weights") or {}).items() if (s.get("origin") or "trained") in store.upload_origins]
    idx = {}
    for b in bases:
        row = con.execute("SELECT name FROM artifact WHERE kind='weights' AND base=? AND status='active' ORDER BY version DESC"
                          " LIMIT 1", (b,)).fetchone()
        if row and uploaded(con, row["name"], store):
            idx[b] = row["name"]
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "index.json"
        f.write_text(json.dumps({"project": cfg.get("project"), "active": idx, "at": now()}, indent=1, sort_keys=True), encoding="utf-8")
        store.put(f, store.key("index.json"), "index: active versions")


def push(con, cfg: dict, notify_fail: bool = True) -> dict:
    store = store_of(cfg)
    if not store:
        return {"store": None, "skipped": "no [weights_store] in reins.toml"}
    done, failed = [], []
    for name in wanted(con, cfg, store):
        if uploaded(con, name, store):
            continue
        try:
            done.append(upload(con, name, store))
        except (ReinsError, subprocess.SubprocessError, OSError) as e:
            failed.append(f"{name}: {e}")
    if done:
        try:
            _write_index(con, cfg, store)
        except (ReinsError, subprocess.SubprocessError, OSError) as e:
            failed.append(f"index.json: {e}")
    if failed and notify_fail:
        notify.send(con, f"weights_push:{cfg.get('project')}", "action", f"{cfg.get('project')}: {len(failed)} weights not uploaded",
                    "\n".join(failed[:10]) + f"\nonly copy is on this machine; fix it, then: reins weights push", cooldown_min=360)
    return {"store": store.label, "uploaded": done, "failed": failed}


def pull(cfg_or_store, dest: Path, names: list[str] | None = None) -> list[dict]:
    """Fetch versions from the store into dest/<base>/..., each file verified against its manifest. Works with no
    registry: the store's index names the active versions."""
    store = cfg_or_store if isinstance(cfg_or_store, Store) else store_of(cfg_or_store)
    if not store:
        raise ReinsError("no [weights_store] in reins.toml")
    dest = Path(dest)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        if not names:
            f = store.get(store.key("index.json"), tmp)
            if not f:
                raise ReinsError(f"{store.label} has no {store.key('index.json')}: nothing pushed yet")
            names = sorted(json.loads(f.read_text())["active"].values())
        out = []
        for name in names:
            mf = store.get(store.key(name, "manifest.json"), tmp)
            if not mf:
                raise ReinsError(f"{store.label} has no {name}")
            man = json.loads(mf.read_text())
            target = dest / man["base"]
            for rel, meta in sorted(man["files"].items()):
                got = store.get(store.key(name, rel), tmp)
                if not got or _sha_file(got) != meta["sha256"]:
                    raise ReinsError(f"{name}/{rel}: missing or its sha256 differs from the manifest; not installed")
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(got), target / rel)
            (target / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            out.append({"name": name, "dir": str(target), "files": len(man["files"]), "bytes": man["bytes"]})
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
