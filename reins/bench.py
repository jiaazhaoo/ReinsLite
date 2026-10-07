"""C4 benchmarks: frozen case sets with inputs, per-stage outputs and graded labels.

    reins bench init NAME --cases FILE [--holdout-fraction 0.2 --seed 7] --about '...'    -> bench-NAME v1 (open)
    reins bench add-stage NAME STAGE --from DIR --module-version V                        copy a stage's outputs in
    reins bench add-input NAME --from DIR                                                 copy inputs in
    reins bench label NAME --file labels.jsonl                                            append labels (labelset += 1)
    reins bench freeze NAME --changelog '...'                                             sha256 everything, read-only
    reins bench bump NAME --changelog '...'                                               vN+1 from vN (hardlinks), open
    reins bench verify NAME [vN]                                                          hashes still match?
    reins bench holdout NAME --why '...' [--tuned]                                        record holdout access / tuning
    reins bench list

Layout: <bench_root>/bench-<name>/v<N>/{MANIFEST.json, cases.csv, inputs/, stages/<stage>/, labels.jsonl, CHANGELOG.md}

Labels (one JSON per line): {"oachargeid", "target", "value", "source", "by", "saw_drawing", "note"}
  source ∈ golden | customer_result | reviewer_verdict | reference_geometry   (trust, high to low)
  golden: by must be a named person and saw_drawing must be true -- never a model, never an edit made in chat.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
from pathlib import Path

from . import names
from .store import ReinsError, config, home, now, session, tx

SOURCES = ("golden", "customer_result", "reviewer_verdict", "reference_geometry")
NOT_PEOPLE = {"", "assistant", "claude", "model", "gemini", "luna", "sol", "pipeline", "auto"}


def full_name(name: str) -> str:
    return name if name.startswith("bench-") else f"bench-{name}"


def _root(name: str) -> Path:
    return Path(config()["bench_root"]) / full_name(name)


def _row(con, name: str, version: int | None = None):
    name = full_name(name)
    q = "SELECT * FROM benchmark WHERE name=?" + ("" if version is None else " AND version=?") + " ORDER BY version DESC LIMIT 1"
    row = con.execute(q, (name,) if version is None else (name, version)).fetchone()
    if not row:
        raise ReinsError(f"benchmark {name}{'' if version is None else ' v' + str(version)} is not registered")
    return row


def _event(con, name: str, version: int, event: str, detail: str = "") -> None:
    con.execute("INSERT INTO benchmark_event (at, name, version, event, detail, session) VALUES (?,?,?,?,?,?)",
                (now(), name, version, event, detail, session()))


def _open(row) -> None:
    if row["status"] == "frozen":
        raise ReinsError(f"{row['name']} v{row['version']} is frozen; `reins bench bump {row['name']}` for a new version")


def _manifest(path: Path) -> dict:
    return json.loads((path / "MANIFEST.json").read_text()) if (path / "MANIFEST.json").exists() else {}


def _write_manifest(path: Path, m: dict) -> None:
    (path / "MANIFEST.json").write_text(json.dumps(m, indent=1, ensure_ascii=False, sort_keys=True))


def init(con, name: str, project: str, case_file: Path, about: str, holdout_fraction: float = 0.2, seed: int = 7,
         case_pattern: str = names.DEFAULT_CASE_PATTERN) -> dict:
    name = full_name(name)
    ids = names.read_case_set(case_file)
    probs = names.case_problems(ids, case_pattern)
    if probs:
        raise ReinsError(f"case set not usable: {probs[:5]}")
    root = _root(name)
    if root.exists():
        raise ReinsError(f"{root} exists; register it or pick another name")
    path = root / "v1"
    (path / "inputs").mkdir(parents=True)
    (path / "stages").mkdir()
    rnd = random.Random(seed)
    shuffled = ids[:]
    rnd.shuffle(shuffled)
    n_hold = round(len(ids) * holdout_fraction)
    holdout = set(shuffled[:n_hold])
    with (path / "cases.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["oachargeid", "split"])
        for i in ids:
            w.writerow([i, "holdout" if i in holdout else "dev"])
    (path / "labels.jsonl").write_text("")
    (path / "CHANGELOG.md").write_text(f"# {name}\n\n## v1 ({now()[:10]})\n{about}\nsplit seed {seed}, holdout {n_hold}/{len(ids)}\n")
    _write_manifest(path, {"name": name, "version": 1, "project": project, "about": about, "created": now(),
                           "split": {"seed": seed, "holdout_fraction": holdout_fraction, "n_dev": len(ids) - n_hold,
                                     "n_holdout": n_hold}, "stages": {}, "labelset": 0, "files": {}})
    with tx(con):
        con.execute("INSERT INTO benchmark (name, version, project, path, status, n_cases, n_dev, n_holdout, created)"
                    " VALUES (?,1,?,?, 'open', ?,?,?,?)", (name, project, str(path), len(ids), len(ids) - n_hold, n_hold, now()))
        _event(con, name, 1, "created", about)
    return {"name": name, "version": 1, "path": str(path), "n_dev": len(ids) - n_hold, "n_holdout": n_hold}


def _copy_tree(src: Path, dst: Path) -> int:
    """Hardlink where possible (same filesystem), copy otherwise. Returns file count."""
    n = 0
    for f in src.rglob("*"):
        if f.is_file():
            t = dst / f.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(f, t)
            except OSError:
                shutil.copy2(f, t)
            n += 1
    return n


def add_stage(con, name: str, stage: str, src: Path, module_version: str) -> int:
    row = _row(con, name)
    _open(row)
    names.check_token("stage", stage)
    from . import modules
    modules.get(con, module_version)
    path = Path(row["path"])
    dst = path / "stages" / stage
    if dst.exists():
        raise ReinsError(f"stage {stage} already in {row['name']} v{row['version']}; bump first")
    n = _copy_tree(src, dst)
    m = _manifest(path)
    m["stages"][stage] = {"module_version": module_version, "from": str(src), "files": n, "added": now()}
    _write_manifest(path, m)
    with tx(con):
        _event(con, row["name"], row["version"], "stage_added", f"{stage} <- {module_version} ({n} files)")
    return n


def add_input(con, name: str, src: Path) -> int:
    row = _row(con, name)
    _open(row)
    n = _copy_tree(src, Path(row["path"]) / "inputs")
    with tx(con):
        _event(con, row["name"], row["version"], "inputs_added", f"{src} ({n} files)")
    return n


def check_label(lab: dict, members: set[str]) -> str | None:
    for k in ("oachargeid", "target", "value", "source", "by"):
        if k not in lab or lab[k] in ("", None):
            return f"missing {k}"
    if lab["oachargeid"] not in members:
        return f"{lab['oachargeid']} is not in the benchmark"
    if lab["source"] not in SOURCES:
        return f"source {lab['source']!r} not in {SOURCES}"
    if lab["source"] == "golden":
        if str(lab["by"]).lower() in NOT_PEOPLE:
            return f"golden needs a named person as `by`, not {lab['by']!r}"
        if lab.get("saw_drawing") is not True:
            return "golden needs saw_drawing: true (judged with the drawing in front of the person)"
    return None


def label(con, name: str, file: Path) -> dict:
    row = _row(con, name)
    _open(row)
    path = Path(row["path"])
    members = {r["oachargeid"] for r in csv.DictReader((path / "cases.csv").open())}
    labs, errors = [], []
    for i, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            lab = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"line {i}: not JSON"); continue
        e = check_label(lab, members)
        if e:
            errors.append(f"line {i}: {e}")
        labs.append(lab)
    if errors:
        raise ReinsError(f"{len(errors)} label problems, nothing appended:\n  " + "\n  ".join(errors[:20]))
    labelset = row["labelset"] + 1
    with (path / "labels.jsonl").open("a", encoding="utf-8") as f:
        for lab in labs:
            f.write(json.dumps({**lab, "labelset": labelset, "at": now(), "session": session()}, ensure_ascii=False) + "\n")
    m = _manifest(path); m["labelset"] = labelset; _write_manifest(path, m)
    with tx(con):
        con.execute("UPDATE benchmark SET labelset=? WHERE name=? AND version=?", (labelset, row["name"], row["version"]))
        _event(con, row["name"], row["version"], "labels_added", f"labelset {labelset}: {len(labs)} labels from {file}")
    by_src = {}
    for lab in labs:
        by_src[lab["source"]] = by_src.get(lab["source"], 0) + 1
    return {"labelset": labelset, "added": len(labs), "by_source": by_src}


def _hash_tree(path: Path) -> dict[str, str]:
    return {str(f.relative_to(path)): names.sha256_file(f) for f in sorted(path.rglob("*"))
            if f.is_file() and f.name != "MANIFEST.json"}


def freeze(con, name: str, changelog: str) -> dict:
    row = _row(con, name)
    _open(row)
    if not changelog.strip():
        raise ReinsError("freeze needs a changelog line (what this version is for)")
    path = Path(row["path"])
    with (path / "CHANGELOG.md").open("a") as f:                 # before hashing: the changelog is part of the freeze
        f.write(f"\nfrozen {now()[:16]}: {changelog}\n")
    files = _hash_tree(path)
    m = _manifest(path)
    m.update(files=files, frozen=now(), changelog=changelog)
    _write_manifest(path, m)
    import subprocess
    subprocess.run(["chmod", "-R", "a-w", str(path)], check=False)
    msha = names.sha256_file(path / "MANIFEST.json")
    with tx(con):
        con.execute("UPDATE benchmark SET status='frozen', frozen=?, manifest_sha=? WHERE name=? AND version=?",
                    (now(), msha, row["name"], row["version"]))
        _event(con, row["name"], row["version"], "frozen", f"{len(files)} files; {changelog}")
    return {"name": row["name"], "version": row["version"], "files": len(files), "manifest_sha": msha}


def adopt(con, name: str, version: int, project: str, path: Path, case_file: Path, holdout_fraction: float = 0.0,
          seed: int = 7, about: str = "") -> dict:
    """An existing benchmark directory becomes bench-<name> v<version>, frozen as it is. Hashes go to
    $REINS_HOME/bench/<name>-v<version>.files.json (the directory is not written to). No holdout unless asked."""
    name = full_name(name)
    if not path.is_dir():
        raise ReinsError(f"{path} is not a directory")
    ids = names.read_case_set(case_file)
    if con.execute("SELECT 1 FROM benchmark WHERE name=? AND version=?", (name, version)).fetchone():
        raise ReinsError(f"{name} v{version} already registered")
    rnd = random.Random(seed); shuffled = ids[:]; rnd.shuffle(shuffled)
    n_hold = round(len(ids) * holdout_fraction); hold = set(shuffled[:n_hold])
    files = _hash_tree(path)
    side = home() / "bench"; side.mkdir(parents=True, exist_ok=True)
    rec = {"name": name, "version": version, "path": str(path), "adopted": now(), "about": about, "files": files,
           "cases": [{"oachargeid": i, "split": "holdout" if i in hold else "dev"} for i in ids]}
    (side / f"{name}-v{version}.files.json").write_text(json.dumps(rec, indent=1))
    msha = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    with tx(con):
        con.execute("INSERT INTO benchmark (name, version, project, path, status, n_cases, n_dev, n_holdout, manifest_sha,"
                    " created, frozen) VALUES (?,?,?,?, 'frozen', ?,?,?,?,?,?)",
                    (name, version, project, str(path), len(ids), len(ids) - n_hold, n_hold, msha, now(), now()))
        _event(con, name, version, "adopted", f"{path}: {len(files)} files hashed; {about}")
    return {"name": name, "version": version, "files": len(files), "n_cases": len(ids)}


def verify(con, name: str, version: int | None = None) -> dict:
    row = _row(con, name, version)
    if row["status"] != "frozen":
        raise ReinsError(f"{row['name']} v{row['version']} is not frozen; nothing to verify against")
    path = Path(row["path"])
    want = _manifest(path).get("files", {})
    side = home() / "bench" / f"{row['name']}-v{row['version']}.files.json"
    if not want and side.exists():                               # adopted: hashes live beside the registry
        want = json.loads(side.read_text())["files"]
    have = _hash_tree(path)
    changed = sorted(k for k in want if have.get(k) != want[k])
    missing = sorted(k for k in want if k not in have)
    extra = sorted(k for k in have if k not in want)
    ok = not (changed or missing or extra) and (
        side.exists() or names.sha256_file(path / "MANIFEST.json") == row["manifest_sha"])
    return {"ok": ok, "changed": changed, "missing": missing, "extra": extra}


def bump(con, name: str, changelog: str) -> dict:
    row = _row(con, name)
    if row["status"] != "frozen":
        raise ReinsError(f"{row['name']} v{row['version']} is still open; freeze it before bumping")
    if not changelog.strip():
        raise ReinsError("bump needs a changelog line (why a new version)")
    src = Path(row["path"])
    new_v = row["version"] + 1
    dst = src.parent / f"v{new_v}"
    if dst.exists():
        raise ReinsError(f"{dst} exists")
    n = _copy_tree(src, dst)
    import subprocess
    subprocess.run(["chmod", "-R", "u+w", str(dst)], check=False)
    # hardlinked files share inodes with the frozen version: break the links for the mutable files
    for f in ("labels.jsonl", "cases.csv", "CHANGELOG.md", "MANIFEST.json"):
        p = dst / f
        if p.exists():
            data = p.read_bytes(); p.unlink(); p.write_bytes(data)
    m = _manifest(dst)
    m.update(version=new_v, created=now(), files={}, previous=row["version"])
    m.pop("frozen", None)
    _write_manifest(dst, m)
    with (dst / "CHANGELOG.md").open("a") as f:
        f.write(f"\n## v{new_v} ({now()[:10]})\n{changelog}\n")
    with tx(con):
        con.execute("INSERT INTO benchmark (name, version, project, path, status, n_cases, n_dev, n_holdout, labelset,"
                    " holdout_compromised, created) VALUES (?,?,?,?, 'open', ?,?,?,?,?,?)",
                    (row["name"], new_v, row["project"], str(dst), row["n_cases"], row["n_dev"], row["n_holdout"],
                     row["labelset"], row["holdout_compromised"], now()))
        _event(con, row["name"], new_v, "bumped", f"from v{row['version']}: {changelog}")
    return {"name": row["name"], "version": new_v, "path": str(dst), "files": n}


def holdout(con, name: str, why: str, tuned: bool = False) -> None:
    """Every look at the holdout split is recorded; tuning on it marks the split compromised (C4 rule 2)."""
    row = _row(con, name)
    with tx(con):
        _event(con, row["name"], row["version"], "holdout_tuned" if tuned else "holdout_access", why)
        if tuned:
            con.execute("UPDATE benchmark SET holdout_compromised=1 WHERE name=? AND version=?", (row["name"], row["version"]))


def cases(con, name: str, version: int | None = None, split: str | None = None) -> list[str]:
    row = _row(con, name, version)
    side = home() / "bench" / f"{row['name']}-v{row['version']}.files.json"
    if side.exists() and not (Path(row["path"]) / "cases.csv").exists():
        rows = json.loads(side.read_text())["cases"]
    else:
        rows = list(csv.DictReader((Path(row["path"]) / "cases.csv").open()))
    return [r["oachargeid"] for r in rows if split is None or r["split"] == split]


def list_(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM benchmark ORDER BY name, version")]


def set_threshold(con, module: str, name: str, value: str, n: int, split: str, benchmark: str | None, by: str,
                  note: str = "") -> dict:
    if split not in ("dev", "holdout", "other"):
        raise ReinsError("split must be dev, holdout or other")
    if split == "holdout":
        raise ReinsError("a threshold set on the holdout split compromises it; use dev, or record `reins bench holdout --tuned`")
    low_n = int(n < 30)
    with tx(con):
        con.execute("INSERT INTO threshold (at, module, name, value, n, split, benchmark, low_n, by_whom, note)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)", (now(), module, name, value, n, split, benchmark, low_n, by, note))
    return {"module": module, "name": name, "value": value, "n": n, "low_n": bool(low_n)}


def thresholds(con, module: str | None = None) -> list[dict]:
    q = "SELECT * FROM threshold" + (" WHERE module=?" if module else "") + " ORDER BY module, name, id"
    rows = [dict(r) for r in con.execute(q, (module,) if module else ())]
    latest = {}
    for r in rows:                                  # the newest row per (module, name) is the one in force
        latest[(r["module"], r["name"])] = r
    return list(latest.values())
