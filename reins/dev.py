"""C2 lifecycle with git: candidate version in its own worktree -> gate -> merge -> released -> release tag.

    reins dev start MODULE SUFFIX --about '...' [--from-batch B --cases ID,ID,...] [--files 'GLOB ...']
    reins dev finish VERSION [--skip-tier T --why '...']
    reins dev abandon VERSION --why '...'
    reins dev release [--note '...']
    reins dev list

Project settings come from the repo's reins.toml:
    project = "e2e-plan-extract"
    [dev]
    repo = "/env/code/e2e-plan-extract"            # main worktree
    gate = "python3 benchmark/qa359/gate.py"       # run in the candidate's worktree; exit 0 = passed. If it writes
                                                   # JSON to $REINS_GATE_OUT, that is recorded as the gate_log row
                                                   # and must be green (see reins/gate.py for the fields)
    [modules.georef]
    about = "place plan images on the map"
    files = ["e2e_plan_extract/georef/*"]
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import gate as gatemod, leases, modules
from .store import ReinsError, now, session, today, tx

IDENT = ["-c", "user.name=reins", "-c", "user.email=reins@local"]


def git(repo: Path, *args: str, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise ReinsError(f"git {' '.join(args)}: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def project_cfg(repo: Path) -> dict:
    import tomllib
    f = repo / "reins.toml"
    if not f.is_file():
        raise ReinsError(f"{repo} has no reins.toml (project = ..., [dev] repo = ..., gate = ...)")
    cfg = tomllib.loads(f.read_text(encoding="utf-8"))
    if "project" not in cfg:
        raise ReinsError(f"{f}: missing project = \"...\"")
    return cfg


def _ensure_module(con, cfg: dict, module: str) -> None:
    if con.execute("SELECT 1 FROM module WHERE name=?", (module,)).fetchone():
        return
    m = cfg.get("modules", {}).get(module)
    if not m:
        raise ReinsError(f"module {module!r} is neither registered nor declared under [modules.{module}] in reins.toml")
    modules.add(con, module, cfg["project"], m.get("about", module))


def overlaps(con, repo: Path, version: str | None, files: list[str]) -> list[str]:
    """Other live candidates whose files overlap these globs."""
    tracked = git(repo, "ls-files").splitlines()
    mine = {f for g in files for f in tracked if fnmatch.fnmatch(f, g)}
    out = []
    for other in con.execute("SELECT version, pins FROM module_version WHERE status='candidate' AND version IS NOT ?", (version,)):
        theirs = {f for g in json.loads(other["pins"]).get("files", []) for f in tracked if fnmatch.fnmatch(f, g)}
        ov = sorted(mine & theirs)
        if ov:
            out.append(f"{other['version']} also changes {ov[:5]}")
    return out


def adopt(con, worktree: Path, module: str, suffix: str, about: str, files: list[str] | None = None,
          overlap_ok: str | None = None) -> dict:
    """An existing worktree (made before reins, e.g. by a project's own dev tool) becomes a candidate version held by
    this session, so its edits are allowed and its merge goes through reins dev finish."""
    w = Path(git(worktree, "rev-parse", "--show-toplevel"))
    repo = Path(git(w, "rev-parse", "--git-common-dir")).resolve().parent
    if w == repo:
        raise ReinsError("the main worktree is never a candidate: reins dev start makes a branch worktree")
    cfg = project_cfg(repo)
    _ensure_module(con, cfg, module)
    if con.execute("SELECT version FROM module_version WHERE worktree=? AND status='candidate'", (str(w),)).fetchone():
        raise ReinsError(f"{w} is already a registered candidate")
    files = files or cfg.get("modules", {}).get(module, {}).get("files", [])
    ov = overlaps(con, repo, None, files)
    if ov and not overlap_ok:
        raise ReinsError("overlaps other work: " + "; ".join(ov) + ". Agree an order, or pass --overlap-ok 'why' (recorded)")
    version = modules.new(con, module, suffix, about, w, {"files": files, "adopted_worktree": str(w),
                                                          "overlap_ok": overlap_ok})
    with tx(con):
        con.execute("UPDATE module_version SET worktree=?, branch=? WHERE version=?",
                    (str(w), git(w, "branch", "--show-current"), version))
    leases.acquire(con, f"worktree:{w}", f"develop {version}")
    modules.note(con, version, "adopted", f"{w}" + (f"; overlap accepted: {overlap_ok}" if overlap_ok else ""))
    return {"version": version, "worktree": str(w)}


def start(con, repo: Path, module: str, suffix: str, about: str, from_batch: str | None = None,
          cases: list[str] | None = None, files: list[str] | None = None, overlap_ok: str | None = None,
          issue: int | None = None) -> dict:
    cfg = project_cfg(repo)
    _ensure_module(con, cfg, module)
    if issue is not None:
        from . import issues
        row = issues.get(con, issue)
        if row["status"] not in ("open", "in_progress"):
            raise ReinsError(f"issue #{issue} is {row['status']}")
        from_batch, cases = row["batch_id"], json.loads(row["cases"])
    files = files or cfg.get("modules", {}).get(module, {}).get("files", [])
    if from_batch:
        from . import batches
        b = batches.get(con, from_batch)
        members = {r[0] for r in con.execute("SELECT oachargeid FROM batch_case WHERE batch_id=?", (from_batch,))}
        bad = [c for c in (cases or []) if c not in members]
        if bad:
            raise ReinsError(f"cases not in {from_batch}: {', '.join(bad)}")
        if not cases:
            raise ReinsError("--from-batch needs --cases: the failing cases are this version's pilot set")
    ov = overlaps(con, repo, None, files)
    if ov and not overlap_ok:
        raise ReinsError("overlaps other work: " + "; ".join(ov) + ". Agree an order, or pass --overlap-ok 'why' (recorded)")
    pins = {"files": files, "from_batch": from_batch, "pilot_cases": cases or [], "forked_from_session": session(),
            "overlap_ok": overlap_ok, "issue": issue}
    version = modules.new(con, module, suffix, about, repo, pins)
    if issue is not None:
        from . import issues
        issues.take(con, issue, version)
    wt = repo.parent / f"{repo.name}-wt-{version}"
    branch = f"feat/{version}"
    tracked = git(repo, "ls-files").splitlines()
    mine = {f for g in files for f in tracked if fnmatch.fnmatch(f, g)}
    warnings = []
    for other in con.execute("SELECT version, pins, session FROM module_version WHERE status='candidate' AND version<>?",
                             (version,)):
        theirs = {f for g in json.loads(other["pins"]).get("files", []) for f in tracked if fnmatch.fnmatch(f, g)}
        ov = sorted(mine & theirs)
        if ov:
            warnings.append(f"{other['version']} (session {other['session']}) also works on {ov[:5]}")
    git(repo, "worktree", "add", "-b", branch, str(wt), "main")
    with tx(con):
        con.execute("UPDATE module_version SET worktree=?, branch=?, commit_sha=? WHERE version=?",
                    (str(wt), branch, git(repo, "rev-parse", "main"), version))
    leases.acquire(con, f"worktree:{wt}", f"develop {version}")
    modules.note(con, version, "worktree", str(wt))
    return {"version": version, "worktree": str(wt), "branch": branch, "warnings": warnings,
            "pilot_cases": cases or []}


def finish(con, version: str, skip_tier: str | None = None, why: str | None = None) -> dict:
    row = modules.get(con, version)
    if row["status"] != "candidate":
        raise ReinsError(f"{version} is {row['status']}")
    wt = Path(row["worktree"] or "")
    if not wt.exists():
        raise ReinsError(f"worktree {wt} is missing")
    leases.check(con, f"worktree:{wt}")
    repo = Path(git(wt, "rev-parse", "--git-common-dir")).resolve().parent
    cfg = project_cfg(repo)
    if git(wt, "status", "--porcelain"):
        raise ReinsError(f"{wt} has uncommitted changes: commit them first")
    r = subprocess.run(["git", *IDENT, "-C", str(wt), "merge", "--no-edit", "main"], capture_output=True, text=True)
    if r.returncode:
        raise ReinsError(f"merging main into {row['branch']} conflicts; resolve in {wt}, commit, finish again")
    gate_cmd = cfg.get("dev", {}).get("gate")
    up = cfg.get("dev", {}).get("upstream") or {}
    base = git(repo, "merge-base", "main", row["branch"])
    changed = git(repo, "diff", "--name-only", base, row["branch"]).splitlines()
    up_hit = [f for f in changed if any(fnmatch.fnmatch(f, g) for g in up.get("files", []))]
    if up_hit and up.get("gate"):
        if skip_tier == "upstream":
            if not why:
                raise ReinsError("--skip-tier upstream needs --why (recorded)")
        else:
            gate_cmd = up["gate"]                         # upstream changes need the upstream gate
    modules.note(con, version, "finish", f"changed {len(changed)} files; upstream {up_hit[:6] or 'none'}; gate {gate_cmd}")
    evidence = ""
    if gate_cmd:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, prefix="reins-gate-") as tf:
            out = tf.name
        env = {**os.environ, "REINS_GATE_OUT": out, "REINS_VERSION": version}
        if skip_tier:
            if not why:
                raise ReinsError("--skip-tier needs --why (recorded)")
            env["REINS_GATE_SKIP"] = skip_tier
        g = subprocess.run(gate_cmd, shell=True, cwd=str(wt), env=env)
        if g.returncode:
            modules.note(con, version, "gate_red", f"exit {g.returncode}")
            raise ReinsError(f"gate failed (exit {g.returncode}); not merged")
        if Path(out).stat().st_size:
            res = json.loads(Path(out).read_text())
            res.setdefault("skipped", f"{skip_tier}: {why}" if skip_tier else None)
            rec = gatemod.record(con, version, **res)
            if rec["status"] != "green":
                raise ReinsError(f"gate red: missed_error {rec['missed_error']} vs baseline {rec['base_missed_error']}, "
                                 f"golden regressions {rec['golden_regressions']}; not merged")
            evidence = f"gate green on {res['benchmark']} tiers {res['tiers']}: missed_error {rec['missed_error']} " \
                       f"(base {rec['base_missed_error']}), review_load {rec['review_load']} (base {rec['base_review_load']})"
        else:
            evidence = f"gate command exit 0 ({gate_cmd})"
        if skip_tier:
            evidence += f"; tier {skip_tier} skipped: {why}"
    else:
        evidence = "no gate configured in reins.toml [dev].gate"
    msg = f"Merge {row['branch']}: {row['about']}\n\n{evidence}\n"
    r = subprocess.run(["git", *IDENT, "-C", str(repo), "merge", "--no-ff", "-m", msg, row["branch"]],
                       capture_output=True, text=True)
    if r.returncode:
        raise ReinsError(f"merge into main failed: {r.stderr[-400:]}")
    from . import artifacts
    head = git(wt, "rev-parse", "--short", "HEAD")
    arts = artifacts.scan(con, wt, cfg, head)                       # prompt / model versions as this candidate has them
    mine = (cfg.get("modules", {}).get(row["module"], {}))
    pinned = [arts[f"prompt:{p}"] for p in mine.get("prompts", []) if f"prompt:{p}" in arts] + \
             [arts[f"model:{m}"] for m in mine.get("models", []) if f"model:{m}" in arts] + \
             [arts[f"weights:{w}"] for w in mine.get("weights", []) if f"weights:{w}" in arts] + \
             [arts[f"tool:{t}"] for t in mine.get("tools", []) if f"tool:{t}" in arts]
    modules.release(con, version, evidence)
    with tx(con):
        pins = json.loads(con.execute("SELECT pins FROM module_version WHERE version=?", (version,)).fetchone()[0])
        pins["artifacts"] = pinned
        con.execute("UPDATE module_version SET commit_sha=?, pins=? WHERE version=?",
                    (git(repo, "rev-parse", "main"), json.dumps(pins, sort_keys=True), version))
        _unbind_sessions(con, version, row["module"])
    for name in pinned:
        if artifacts.get(con, name)["status"] == "candidate":
            artifacts.set_status(con, name, "active", f"released with {version}")
    evidence += ("; artifacts " + ", ".join(pinned)) if pinned else ""
    for m, spec in (project_cfg(repo).get("modules") or {}).items():   # the module line on the board follows reins.toml
        cur = con.execute("SELECT about FROM module WHERE name=?", (m,)).fetchone()
        if cur and spec.get("about") and spec["about"] != cur["about"]:
            try:
                modules.describe_module(con, m, spec["about"])
            except ReinsError:
                pass
    for wf in (project_cfg(repo).get("workflows") or {}):          # the workflow follows production: new version if anything moved
        try:
            fr = artifacts.freeze_workflow(con, repo, project_cfg(repo), wf)
            evidence += f"; {fr['name']}" + (" (new)" if fr["new"] else "")
        except ReinsError as e:
            evidence += f"; workflow {wf} not refrozen: {e}"
    from . import issues
    issues.fixed(con, version)
    git(repo, "worktree", "remove", "--force", str(wt), check=False)
    git(repo, "branch", "-d", row["branch"], check=False)
    leases.release(con, f"worktree:{wt}", force=True)
    return {"version": version, "main": git(repo, "rev-parse", "--short", "main"), "evidence": evidence}


def _unbind_sessions(con, version: str, module: str) -> None:
    """The work is over: sessions bound to this version (or this session, to its module) stop showing as in progress."""
    con.execute("UPDATE session SET version=NULL, module=NULL, role='analysis' WHERE version=? OR (id=? AND module=?)",
                (version, session(), module))


def abandon(con, version: str, why: str) -> None:
    row = modules.get(con, version)
    if row["status"] != "candidate":
        raise ReinsError(f"{version} is {row['status']}")
    with tx(con):
        con.execute("UPDATE module_version SET status='abandoned' WHERE version=?", (version,))
        _unbind_sessions(con, version, row["module"])
    modules.note(con, version, "abandoned", why)
    if row["worktree"]:
        leases.release(con, f"worktree:{row['worktree']}", force=True)


def release(con, repo: Path, note: str = "") -> dict:
    cfg = project_cfg(repo)
    project = cfg["project"]
    day = today()
    with tx(con):
        prefix = cfg.get("dev", {}).get("release_prefix") or f"rel-{project}"
        taken = set(git(repo, "tag", "-l", f"{prefix}-{day}-*").split())
        n = 1
        while f"{prefix}-{day}-{n}" in taken:
            n += 1
        name = f"{prefix}-{day}-{n}"
        git(repo, *IDENT, "tag", "-a", name, "-m", note or name, "main")
        wt = repo.parent / (f"{repo.name}-{name}" if name.startswith("rel-") else f"{repo.name}-rel-{name}")
        git(repo, "worktree", "add", "--detach", str(wt), name)
        subprocess.run(["chmod", "-R", "a-w", str(wt)], check=False)
        probe = next((f for f in wt.rglob("*") if f.is_file()), None)
        if probe is not None and os.access(probe, os.W_OK):
            # /env is NTFS through fuseblk: chmod is accepted and ignored. The tree is protected by the hook alone.
            note = (note + "; " if note else "") + "filesystem ignores chmod: release tree guarded by the reins hook only"
        versions = [r[0] for r in con.execute(
            "SELECT version FROM module_version v JOIN module m ON m.name=v.module WHERE m.project=? AND v.status='released'"
            " AND v.day || '-' || printf('%09d', v.seq) = (SELECT MAX(day || '-' || printf('%09d', seq)) FROM module_version x"
            " WHERE x.module=v.module AND x.status='released')", (project,))]
        con.execute("INSERT INTO release (name, project, commit_sha, worktree, note, versions, created, session)"
                    " VALUES (?,?,?,?,?,?,?,?)", (name, project, git(repo, "rev-parse", name), str(wt), note,
                                                  json.dumps(versions), now(), session()))
    return {"name": name, "worktree": str(wt), "versions": versions}


def adopt_release(con, repo: Path, tag: str, note: str = "") -> dict:
    """An existing git tag with its read-only worktree becomes a registered release (name = the tag)."""
    cfg = project_cfg(repo) if (repo / "reins.toml").is_file() else None
    project = cfg["project"] if cfg else repo.name
    commit = git(repo, "rev-parse", f"{tag}^{{commit}}")
    wt, best = None, -1
    for block in git(repo, "worktree", "list", "--porcelain").split("\n\n"):
        lines = dict(l.split(" ", 1) if " " in l else (l, "") for l in block.splitlines())
        if lines.get("HEAD") != commit or lines.get("worktree") == str(repo):
            continue
        # a release tree is detached and usually named after the tag; a branch worktree at the same commit is not one
        score = ("detached" in lines) * 2 + (tag in lines.get("worktree", ""))
        if score > best:
            wt, best = lines["worktree"], score
    if wt and best < 2:
        raise ReinsError(f"only a branch worktree sits at {tag} ({wt}); a release tree must be a detached checkout")
    if not wt:
        raise ReinsError(f"no worktree checked out at {tag} ({commit[:9]}); release trees must exist read-only")
    with tx(con):
        if con.execute("SELECT 1 FROM release WHERE name=?", (tag,)).fetchone():
            raise ReinsError(f"release {tag} already registered")
        con.execute("INSERT INTO release (name, project, commit_sha, worktree, note, versions, created, session)"
                    " VALUES (?,?,?,?,?,?,?,?)", (tag, project, commit, wt, note or git(repo, "tag", "-l", "--format=%(contents:subject)", tag),
                                                  "[]", now(), session()))
    return {"name": tag, "commit": commit[:9], "worktree": wt}


def list_(con, project: str | None = None) -> dict:
    q = "SELECT v.*, m.project FROM module_version v JOIN module m ON m.name=v.module"
    args = ()
    if project:
        q += " WHERE m.project=?"; args = (project,)
    rows = [dict(r) for r in con.execute(q + " ORDER BY v.module, v.day, v.seq", args)]
    rels = [dict(r) for r in con.execute("SELECT * FROM release" + (" WHERE project=?" if project else "") +
                                         " ORDER BY created DESC LIMIT 5", args)]
    return {"candidates": [r for r in rows if r["status"] == "candidate"],
            "released": [r for r in rows if r["status"] == "released"], "releases": rels}
