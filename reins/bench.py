"""C4 benchmarks: frozen case sets with inputs, per-stage outputs and graded labels.

    reins bench init NAME --cases FILE [--holdout-fraction 0.2 --seed 7] --about '...'    -> bench-NAME v1 (open)
    reins bench add-stage NAME STAGE --from DIR --module-version V                        copy a stage's outputs in
    reins bench add-input NAME --from DIR                                                 copy inputs in
    reins bench label NAME --file labels.jsonl                                            append labels (labelset += 1)
    reins bench freeze NAME --changelog '...'                                             sha256 everything, read-only
    reins bench bump NAME --changelog '...'                                               vN+1 from vN (hardlinks, symlinks kept), open
    reins bench verify NAME [vN]                                                          hashes still match?
    reins bench holdout NAME --why '...' [--tuned]                                        record holdout access / tuning
    reins bench list
    reins bench changed NAME --stage STAGE --dir OUT [--frozen REL] [--ignore GLOB]...    cases whose outputs differ
    reins bench add-cases NAME (--cases FILE | --issue N) --split dev|holdout --why '...' grow an open version

Incremental downstream (2026-10-19): a module gate re-runs the stages after the changed module only for the cases whose
outputs of that module changed; every other case keeps its frozen downstream outputs (same inputs, same outputs).
`changed` compares per-case tree hashes of the new outputs with the frozen ones and says which cases those are.

Layout: <bench_root>/bench-<name>/v<N>/{MANIFEST.json, cases.csv, inputs/, stages/<stage>/, labels.jsonl, CHANGELOG.md}

Bump: files of vN+1 are hardlinks to vN's (same inode, so the same bytes and the same read-only mode) and symlinks
are copied as symlinks with the same target (v10's `inputs -> ../v1/inputs` stays exactly that in v11). Only
labels.jsonl, cases.csv, CHANGELOG.md and MANIFEST.json get their own inode. To change any other file in an open
version, unlink it first and write a new file: writing in place through the hardlink would change the frozen version.

Labels (one JSON per line): {"case_id", "target", "value", "source", "by", "saw_drawing", "note"}
  source ∈ golden | customer_result | reviewer_verdict | reference[_<kind>]   (trust, high to low)
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

SOURCES = ("golden", "customer_result", "reviewer_verdict", "reference")   # reference_<kind> (reference_geometry, ...) is reference
NOT_PEOPLE = {"", "assistant", "claude", "model", "llm", "ai", "pipeline", "auto", "bot"}


def _models(con) -> set[str]:
    """Names a model goes by in this registry (artifact base, model id and its last part): never a golden labeller."""
    out = set()
    try:
        for r in con.execute("SELECT base, body FROM artifact WHERE kind='model'"):
            out.add(r["base"].lower())
            mid = str(json.loads(r["body"]).get("id", "")).lower()
            if mid:
                out |= {mid, mid.split("/")[-1]} | set(mid.split("/")[-1].replace("_", "-").split("-"))
    except Exception:                                                      # noqa: BLE001  (no artifact table yet)
        pass
    return {m for m in out if len(m) > 2 and not m.isdigit()}


def _cid(row: dict, key: str | None = None) -> str:
    """Case id of a cases.csv / sidecar row: case_id (or the project's key); reins writes the case column first, so a
    benchmark frozen before the framework used case_id still reads."""
    return names.case_of(row, key) or next(iter(row.values()))


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
         case_pattern: str = names.DEFAULT_CASE_PATTERN, unit: str = "case") -> dict:
    """unit: what one row scores. 'case' (default) = the project's case, held to its case id pattern; anything else
    (page, crop, ...) is a module benchmark's own unit: ids must only be non-empty and unique."""
    name = full_name(name)
    ids = names.read_case_set(case_file)
    if unit != "case":
        names.check_token("unit", unit)
        case_pattern = names.DEFAULT_CASE_PATTERN
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
        w = csv.writer(f); w.writerow(["case_id", "split"])
        for i in ids:
            w.writerow([i, "holdout" if i in holdout else "dev"])
    (path / "labels.jsonl").write_text("")
    (path / "CHANGELOG.md").write_text(f"# {name}\n\n## v1 ({now()[:10]})\n{about}\nsplit seed {seed}, holdout {n_hold}/{len(ids)}\n")
    _write_manifest(path, {"name": name, "version": 1, "project": project, "about": about, "created": now(),
                           "split": {"seed": seed, "holdout_fraction": holdout_fraction, "n_dev": len(ids) - n_hold,
                                     "n_holdout": n_hold}, "unit": unit, "stages": {}, "labelset": 0, "files": {}})
    with tx(con):
        con.execute("INSERT INTO benchmark (name, version, project, path, status, n_cases, n_dev, n_holdout, created)"
                    " VALUES (?,1,?,?, 'open', ?,?,?,?)", (name, project, str(path), len(ids), len(ids) - n_hold, n_hold, now()))
        _event(con, name, 1, "created", about)
    return {"name": name, "version": 1, "path": str(path), "n_dev": len(ids) - n_hold, "n_holdout": n_hold}


def _copy_tree(src: Path, dst: Path, keep_links: bool = False) -> int:
    """Hardlink where possible (same filesystem), copy otherwise. Returns the number of regular files.
    keep_links (bump): a symlink stays a symlink with the same target. Otherwise (add-stage / add-input, whose
    relative links would not resolve from the benchmark) symlinks are followed and their contents copied in.
    A symlinked directory is never silently dropped."""
    n = 0
    for d, dirs, files in os.walk(src, followlinks=not keep_links):
        d = Path(d)
        (dst / d.relative_to(src)).mkdir(parents=True, exist_ok=True)
        for e in dirs + files:
            f = d / e
            t = dst / f.relative_to(src)
            if keep_links and f.is_symlink():
                os.symlink(os.readlink(f), t)
            elif f.is_file():
                try:
                    os.link(f.resolve(), t)              # a symlinked file: link its target (os.link would link the symlink)
                except OSError:
                    shutil.copy2(f, t)
                n += 1
    return n


def _links_under(path: Path) -> list[Path]:
    return sorted(Path(d) / e for d, dirs, files in os.walk(path) for e in dirs + files if (Path(d) / e).is_symlink())


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


def check_label(lab: dict, members: set[str], key: str | None = None, not_people: set[str] = frozenset()) -> str | None:
    cid = names.case_of(lab, key)
    if cid is None:
        return f"missing case_id{' (or ' + key + ')' if key else ''}"
    lab["case_id"] = cid                                  # stored under the framework's name
    if key and key != "case_id":
        lab.pop(key, None)
    for k in ("target", "value", "source", "by"):
        if k not in lab or lab[k] in ("", None):
            return f"missing {k}"
    if lab["case_id"] not in members:
        return f"{lab['case_id']} is not in the benchmark"
    if lab["source"] not in SOURCES and not str(lab["source"]).startswith("reference_"):
        return f"source {lab['source']!r} not in {SOURCES}"
    if lab["source"] == "golden":
        if str(lab["by"]).lower() in NOT_PEOPLE | set(not_people):
            return f"golden needs a named person as `by`, not {lab['by']!r}"
        if lab.get("saw_drawing") is not True:
            return "golden needs saw_drawing: true (judged with the drawing in front of the person)"
    return None


def label(con, name: str, file: Path, key: str | None = None) -> dict:
    row = _row(con, name)
    _open(row)
    path = Path(row["path"])
    with (path / "cases.csv").open() as f:
        members = {_cid(r, key) for r in csv.DictReader(f)}
    models = _models(con)
    labs, errors = [], []
    for i, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            lab = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"line {i}: not JSON"); continue
        e = check_label(lab, members, key, models)
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
           "cases": [{"case_id": i, "split": "holdout" if i in hold else "dev"} for i in ids]}
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
    n = _copy_tree(src, dst, keep_links=True)
    # The new version's files share inodes with the frozen one, so a chmod on them is a chmod on vN (2026-10-15:
    # `chmod -R u+w` here made every v10 file writable). Keep the shared inodes read-only -- that also re-protects
    # an adopted vN whose files were never made read-only -- and give only the files bump writes their own inode.
    reprotected = 0
    for p in dst.rglob("*"):
        if not p.is_symlink() and p.is_file() and p.stat().st_mode & 0o222:
            os.chmod(p, p.stat().st_mode & ~0o222); reprotected += 1
    for f in ("labels.jsonl", "cases.csv", "CHANGELOG.md", "MANIFEST.json"):
        p = dst / f
        if p.exists() and not p.is_symlink():
            data = p.read_bytes(); p.unlink(); p.write_bytes(data)
    links = _links_under(dst)
    dangling = [str(p.relative_to(dst)) for p in links if not p.exists()]
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
        _event(con, row["name"], new_v, "bumped", f"from v{row['version']}: {n} files, {len(links)} symlinks; {changelog}")
    return {"name": row["name"], "version": new_v, "path": str(dst), "files": n, "symlinks": len(links),
            "dangling_symlinks": dangling, "reprotected": reprotected}


def _case_dirs(root: Path, ids: list[str]) -> dict[str, Path]:
    """Case id -> its directory under a stage output root: the directory named like the case, or ending in _<case>
    (a project's display prefix, e.g. ABC_123)."""
    if not root.is_dir():
        return {}
    want = set(ids)
    out = {}
    for d in root.iterdir():
        if not d.is_dir():
            continue
        cid = d.name if d.name in want else d.name.rsplit("_", 1)[-1]
        if cid in want and cid not in out:
            out[cid] = d
    return out


def _tree_sha(d: Path, ignore: list[str]) -> str:
    import fnmatch
    h = hashlib.sha256()
    for f in sorted(d.rglob("*")):
        rel = str(f.relative_to(d))
        if not f.is_file() or any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(f.name, g) for g in ignore):
            continue
        h.update(rel.encode() + b"\0" + names.sha256_file(f).encode() + b"\n")
    return h.hexdigest()


def changed(con, name: str, stage: str, out_dir: Path, version: int | None = None, frozen_rel: str | None = None,
            ignore: list[str] | None = None) -> dict:
    """Which cases' outputs of one stage differ between a new run (out_dir/<case>/...) and the benchmark's frozen
    outputs (stages/<stage>/<case>/..., or frozen_rel for a layout adopted from a project). Per-case tree hashes of the
    files (paths + content); ignore drops volatile files (logs, timings). -> {changed, same, new, missing}: only the
    changed and new cases need their downstream re-run."""
    row = _row(con, name, version)
    if row["status"] != "frozen":
        raise ReinsError(f"{row['name']} v{row['version']} is open: compare against a frozen version")
    ids = cases(con, name, row["version"])
    frozen_root = Path(row["path"]) / (frozen_rel or f"stages/{stage}")
    old, new = _case_dirs(frozen_root, ids), _case_dirs(Path(out_dir), ids)
    ig = list(ignore or [])
    res = {"benchmark": f"{row['name']}-v{row['version']}", "stage": stage, "changed": [], "same": [], "new": [],
           "missing": []}
    for c in ids:
        a, b = old.get(c), new.get(c)
        if a is None and b is None:
            continue
        if b is None:
            res["missing"].append(c)
        elif a is None:
            res["new"].append(c)
        elif _tree_sha(a, ig) == _tree_sha(b, ig):
            res["same"].append(c)
        else:
            res["changed"].append(c)
    return res


def add_cases(con, name: str, ids: list[str], split: str, why: str, source: str = "") -> dict:
    """Grow an open version: cases join the dev split (regressions found in production, a council not covered yet) or
    the holdout split (fresh, never-tuned-on cases). Recorded in CHANGELOG and the event log; frozen versions never change."""
    if split not in ("dev", "holdout"):
        raise ReinsError("split is dev or holdout")
    if len(why.strip()) < 6:
        raise ReinsError("add-cases needs --why (why these cases, where from)")
    row = _row(con, name)
    _open(row)
    path = Path(row["path"])
    have = set(cases(con, name, row["version"]))
    add = [c for c in dict.fromkeys(ids) if c not in have]
    if not add:
        return {"added": 0, "already": len(ids)}
    with (path / "cases.csv").open("a", newline="") as f:
        w = csv.writer(f)
        for c in add:
            w.writerow([c, split])
    with (path / "CHANGELOG.md").open("a") as f:
        f.write(f"- {now()[:10]} +{len(add)} {split} cases" + (f" ({source})" if source else "") + f": {why}\n")
    nd, nh = (len(add), 0) if split == "dev" else (0, len(add))
    with tx(con):
        con.execute("UPDATE benchmark SET n_cases=n_cases+?, n_dev=n_dev+?, n_holdout=n_holdout+? WHERE name=? AND version=?",
                    (len(add), nd, nh, row["name"], row["version"]))
        _event(con, row["name"], row["version"], "cases_added", f"+{len(add)} {split}{' from ' + source if source else ''}: {why}")
    return {"added": len(add), "already": len(ids) - len(add), "split": split}


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
    return [_cid(r) for r in rows if split is None or r["split"] == split]


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
