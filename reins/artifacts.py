"""C17 artifacts: versioned things a workflow is made of, besides code -- prompts, models, workflows.

Several versions of each may be in use at once (a judge prompt v3 in production while v4 is on trial); what ties
them together is the pin: a module version pins the prompt and model versions it uses, a workflow version pins the
module / prompt / model versions of every stage, a batch records the workflow version it ran.

    prompt-<name>-vN     content-addressed: the same text is the same version; a changed text is a new version
    model-<name>-vN      provider + model id + fixed parameters; a changed parameter is a new version
    workflow-<name>-vN   ordered stages, each with its module version and the prompt / model versions it uses

Where the content comes from is declared in the project's reins.toml, so code is not rewritten to be versioned:
    [prompts.judge]        file = "tools/qa_judge.py"   symbol = "PROMPT"      # a Python string constant
    [prompts.crops]        file = "prompts/crops.md"                            # or a whole file
    [models.judge_primary] provider = "openrouter"  id = "google/gemini-3.8-flash"  params = {temperature = 0}
    [workflows.local_qa]   about = "..."  stages = [{name = "check", module = "judge", prompts = ["judge"], models = ["judge_primary"], paid = true}, ...]

    reins artifact scan [--repo DIR]                 register the current prompt / model versions declared in reins.toml
    reins artifact list [prompt|model|workflow]
    reins artifact show NAME-vN                      content / spec and where it is pinned
    reins workflow freeze NAME [--repo DIR]          workflow-NAME-vN from the production module versions + current prompts / models
    reins artifact diff A B                          two versions of the same artifact
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from pathlib import Path

from .store import ReinsError, now, session, tx

KINDS = ("prompt", "model", "workflow", "weights")
NAME = re.compile(r"^(prompt|model|workflow|weights)-([a-z][a-z0-9_]*)-v(\d+)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact (
  name     TEXT PRIMARY KEY,                           -- <kind>-<base>-vN
  kind     TEXT NOT NULL,
  base     TEXT NOT NULL,
  version  INTEGER NOT NULL,
  sha      TEXT NOT NULL,                              -- content hash: same content = same version
  status   TEXT NOT NULL CHECK (status IN ('candidate', 'active', 'retired')),
  about    TEXT,
  body     TEXT NOT NULL,                              -- prompt text | model spec JSON | workflow spec JSON
  source   TEXT,                                       -- where it was read from (file, symbol, commit)
  created  TEXT NOT NULL,
  session  TEXT,
  UNIQUE (kind, base, version)
);
CREATE TABLE IF NOT EXISTS file_sha (                   -- hash cache: big weight files are not re-read every scan
  path   TEXT PRIMARY KEY,
  size   INTEGER NOT NULL,
  mtime  REAL NOT NULL,
  sha    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_event (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  name      TEXT NOT NULL,
  event     TEXT NOT NULL,
  detail    TEXT,
  session   TEXT
);
"""


def ensure(con) -> None:
    """Create any missing table. Statement by statement (executescript would commit an open transaction), and
    checked per table, so a table added later (file_sha) appears in a registry that already had the others."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {"artifact", "artifact_event", "file_sha"} <= have:
        return
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            con.execute(stmt)


def parse(name: str) -> tuple[str, str, int]:
    m = NAME.match(name)
    if not m:
        raise ReinsError(f"{name!r} is not <prompt|model|workflow>-<name>-vN")
    return m.group(1), m.group(2), int(m.group(3))


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def register(con, kind: str, base: str, body: str, about: str | None = None, source: str | None = None,
             status: str = "candidate") -> dict:
    """The version for this content: existing when the hash is known, otherwise the next number. Idempotent."""
    ensure(con)
    if kind not in KINDS:
        raise ReinsError(f"kind must be one of {KINDS}")
    if not re.match(r"^[a-z][a-z0-9_]*$", base):
        raise ReinsError(f"artifact name {base!r}: lowercase letters, digits and _")
    sha = _sha(body)
    with tx(con):
        same = con.execute("SELECT name FROM artifact WHERE kind=? AND base=? AND sha=?", (kind, base, sha)).fetchone()
        if same:
            return {"name": same["name"], "new": False}
        v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM artifact WHERE kind=? AND base=?", (kind, base)).fetchone()[0]
        name = f"{kind}-{base}-v{v}"
        con.execute("INSERT INTO artifact (name, kind, base, version, sha, status, about, body, source, created, session)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (name, kind, base, v, sha, status, about, body, source, now(), session()))
        con.execute("INSERT INTO artifact_event (at, name, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), name, "registered", source or "", session()))
    return {"name": name, "new": True}


def get(con, name: str):
    ensure(con)
    parse(name)
    row = con.execute("SELECT * FROM artifact WHERE name=?", (name,)).fetchone()
    if not row:
        raise ReinsError(f"artifact {name!r} is not registered")
    return row


def set_status(con, name: str, status: str, why: str = "") -> None:
    if status not in ("candidate", "active", "retired"):
        raise ReinsError("status is candidate | active | retired")
    with tx(con):
        get(con, name)
        con.execute("UPDATE artifact SET status=? WHERE name=?", (status, name))
        con.execute("INSERT INTO artifact_event (at, name, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), name, status, why, session()))


# ------------------------------------------------------------------ reading content from a project
def read_symbol(path: Path, symbol: str) -> str:
    """The value of a module-level string constant (PROMPT = \"\"\"...\"\"\"), without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == symbol for t in targets):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            if isinstance(value, ast.JoinedStr):
                raise ReinsError(f"{path}:{symbol} is an f-string; the prompt text must be a plain constant to be versioned")
            try:
                v = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                raise ReinsError(f"{path}:{symbol} is not a string literal (is it built at runtime?)") from None
            if isinstance(v, str):
                return v
            if isinstance(v, (tuple, list)) and all(isinstance(x, str) for x in v):
                return "".join(v)
    raise ReinsError(f"{path}: no module-level constant {symbol}")


def prompt_text(repo: Path, spec: dict) -> tuple[str, str]:
    f = repo / spec["file"]
    if not f.is_file():
        raise ReinsError(f"prompt file {f} does not exist")
    if spec.get("symbol"):
        return read_symbol(f, spec["symbol"]), f"{spec['file']}:{spec['symbol']}"
    return f.read_text(encoding="utf-8"), spec["file"]


# ------------------------------------------------------------------ weights: ML model files
def file_sha(con, path: Path) -> tuple[str, int]:
    """sha256 of a file, cached by (size, mtime); symlinks are followed (the target is the weight)."""
    p = Path(path).resolve()
    st = p.stat()
    row = con.execute("SELECT sha FROM file_sha WHERE path=? AND size=? AND mtime=?", (str(p), st.st_size, st.st_mtime)).fetchone()
    if row:
        return row["sha"], st.st_size
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    with tx(con):
        con.execute("INSERT OR REPLACE INTO file_sha VALUES (?,?,?,?)", (str(p), st.st_size, st.st_mtime, h.hexdigest()))
    return h.hexdigest(), st.st_size


WEIGHT_EXT = {".pt", ".pth", ".ckpt", ".onnx", ".engine", ".safetensors", ".bin", ".pdiparams", ".pdmodel", ".json", ".yaml", ".yml", ".txt"}


def weight_files(spec: dict) -> list[Path]:
    """The files that make up one weights artifact: a single file, or every model file under a directory."""
    if spec.get("file"):
        p = Path(spec["file"])
        if not p.exists():
            raise ReinsError(f"weights file {p} does not exist")
        return [p]
    if spec.get("dir"):
        d = Path(spec["dir"])
        if not d.is_dir():
            raise ReinsError(f"weights dir {d} does not exist")
        files = sorted(f for f in d.rglob("*") if f.is_file() and f.suffix.lower() in WEIGHT_EXT and not f.name.startswith("."))
        if not files:
            raise ReinsError(f"no model files under {d}")
        return files
    raise ReinsError("a weights entry needs file = ... or dir = ...")


def _read_json(p: Path, limit: int = 4000):
    try:
        return json.loads(p.read_text(encoding="utf-8")[:limit * 50])
    except Exception:                                                       # noqa: BLE001
        return None


def harvest_training(primary: Path) -> dict:
    """What a training run left beside its weights: ultralytics (args.yaml, results.csv), the house ConvNeXt trainer
    (history.json, metrics.json, best_model_test_metrics.json, train_manifest.csv, split_counts.json), a sibling .json."""
    out: dict = {}
    d = primary.parent
    run = d.parent if d.name == "weights" else d                       # ultralytics: <run>/weights/best.pt
    args = run / "args.yaml"
    if args.is_file():
        kv = {}
        for line in args.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                kv[k.strip()] = v.strip()
        out["framework"] = "ultralytics"
        out["train"] = {k: kv.get(k) for k in ("task", "model", "data", "epochs", "imgsz", "batch", "seed", "name") if k in kv}
    res = run / "results.csv"
    if res.is_file():
        rows = [l for l in res.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        if len(rows) >= 2:
            hdr = [h.strip() for h in rows[0].split(",")]
            last = [v.strip() for v in rows[-1].split(",")]
            want = {"epoch", "metrics/mAP50(B)", "metrics/mAP50-95(B)", "metrics/precision(B)", "metrics/recall(B)",
                    "metrics/mAP50(M)", "metrics/mAP50-95(M)"}
            out["metrics"] = {h: last[i] for i, h in enumerate(hdr) if h in want and i < len(last)}
            out["epochs_run"] = len(rows) - 1
    for name in ("metrics.json", "best_model_test_metrics.json"):
        j = _read_json(d / name)
        if isinstance(j, dict):
            out.setdefault("metrics", {}).update({k: v for k, v in j.items() if isinstance(v, (int, float, str))})
    hist = _read_json(d / "history.json")
    if isinstance(hist, list) and hist and isinstance(hist[-1], dict):
        out["framework"] = out.get("framework", "house-trainer")
        out["epochs_run"] = len(hist)
        out.setdefault("metrics", {}).update({k: v for k, v in hist[-1].items() if isinstance(v, (int, float))})
    man = d / "train_manifest.csv"
    if man.is_file():
        n = sum(1 for _ in man.open(encoding="utf-8", errors="ignore")) - 1
        out["dataset"] = {"manifest": str(man), "rows": n}
    sc = _read_json(d / "split_counts.json")
    if isinstance(sc, dict):
        out.setdefault("dataset", {})["splits"] = sc
    side = d / (primary.stem + ".json")
    j = _read_json(side)
    if isinstance(j, dict):
        out["sidecar"] = {k: j[k] for k in list(j)[:20]}
    return out


def weights_body(con, spec: dict) -> tuple[str, dict]:
    files = weight_files(spec)
    root = Path(spec.get("dir") or Path(spec["file"]).parent)
    rec = {}
    for f in files:
        sha, size = file_sha(con, f)                        # hashes the symlink target (HF snapshots point into blobs/)
        rec[str(f.relative_to(root)) if spec.get("dir") else f.name] = {"sha256": sha, "size": size}
    identity = json.dumps(rec, sort_keys=True)                           # the version is the files, not the notes
    body = {"files": rec, "path": str(root.resolve()), "n_files": len(files), "bytes": sum(v["size"] for v in rec.values()),
            "config_key": spec.get("config_key"), "training": {**harvest_training(files[0]), **(spec.get("training") or {})}}
    return identity, body


def register_weights(con, base: str, spec: dict, about: str | None = None) -> dict:
    identity, body = weights_body(con, spec)
    sha = _sha(identity)
    ensure(con)
    with tx(con):
        same = con.execute("SELECT name FROM artifact WHERE kind='weights' AND base=? AND sha=?", (base, sha)).fetchone()
        if same:
            return {"name": same["name"], "new": False, "bytes": body["bytes"]}
        v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM artifact WHERE kind='weights' AND base=?", (base,)).fetchone()[0]
        name = f"weights-{base}-v{v}"
        con.execute("INSERT INTO artifact (name, kind, base, version, sha, status, about, body, source, created, session)"
                    " VALUES (?,?,?,?,?, 'candidate', ?,?,?,?,?)",
                    (name, "weights", base, v, sha, about or spec.get("about"), json.dumps(body, sort_keys=True), body["path"], now(), session()))
        con.execute("INSERT INTO artifact_event (at, name, event, detail, session) VALUES (?,?,?,?,?)",
                    (now(), name, "registered", f"{body['n_files']} files, {body['bytes'] / 1e6:.1f} MB", session()))
    return {"name": name, "new": True, "bytes": body["bytes"]}


def check_weights(con, cfg: dict) -> list[tuple[str, bool, str]]:
    """Before a batch: every declared weights file exists and is a registered version (nothing silently swapped)."""
    out = []
    for base, spec in (cfg.get("weights") or {}).items():
        try:
            identity, body = weights_body(con, spec)
        except ReinsError as e:
            out.append((f"weights_{base}", False, str(e))); continue
        row = con.execute("SELECT name, status FROM artifact WHERE kind='weights' AND base=? AND sha=?", (base, _sha(identity))).fetchone()
        if not row:
            latest = con.execute("SELECT name FROM artifact WHERE kind='weights' AND base=? ORDER BY version DESC LIMIT 1", (base,)).fetchone()
            out.append((f"weights_{base}", False, f"file content is not a registered version (latest {latest['name'] if latest else 'none'}): "
                                                   f"run reins artifact scan, or restore the file"))
        else:
            out.append((f"weights_{base}", True, f"{row['name']} [{row['status']}] {body['bytes'] / 1e6:.0f} MB"))
    return out


def model_body(spec: dict) -> str:
    if "id" not in spec or "provider" not in spec:
        raise ReinsError("a model needs provider and id")
    return json.dumps({"provider": spec["provider"], "id": spec["id"], "params": spec.get("params", {})}, sort_keys=True)


def scan(con, repo: Path, cfg: dict, commit: str | None = None) -> dict:
    """Register the prompt and model versions a project declares, as they are now. Returns {name: artifact}."""
    out = {}
    for base, spec in (cfg.get("prompts") or {}).items():
        text, src = prompt_text(repo, spec)
        r = register(con, "prompt", base, text, spec.get("about"), f"{src}@{commit or 'worktree'}")
        out[f"prompt:{base}"] = r["name"]
    for base, spec in (cfg.get("models") or {}).items():
        r = register(con, "model", base, model_body(spec), spec.get("about"), "reins.toml")
        out[f"model:{base}"] = r["name"]
    for base, spec in (cfg.get("weights") or {}).items():
        r = register_weights(con, base, spec)
        out[f"weights:{base}"] = r["name"]
    return out


def freeze_workflow(con, repo: Path, cfg: dict, base: str) -> dict:
    """workflow-<base>-vN: every stage with its production module version and current prompt / model versions."""
    from . import modules
    wf = (cfg.get("workflows") or {}).get(base)
    if not wf:
        raise ReinsError(f"no [workflows.{base}] in reins.toml")
    current = scan(con, repo, cfg)
    stages = []
    for st in wf["stages"]:
        mod = st.get("module")
        mv = None
        if mod:
            p = modules.status(con, mod)["production"]
            if not p:
                raise ReinsError(f"module {mod} has no released version; release it before freezing the workflow")
            mv = p["version"]
        stages.append({"name": st["name"], "module_version": mv, "paid": bool(st.get("paid")),
                       "prompts": [current[f"prompt:{p}"] for p in st.get("prompts", [])],
                       "models": [current[f"model:{m}"] for m in st.get("models", [])],
                       "weights": [current[f"weights:{w}"] for w in st.get("weights", [])]})
    body = json.dumps({"stages": stages}, sort_keys=True)
    r = register(con, "workflow", base, body, wf.get("about"), "reins.toml", status="active")
    if r["new"]:
        for name in {a for s in stages for a in s["prompts"] + s["models"] + s["weights"]}:
            row = get(con, name)
            if row["status"] == "candidate":
                set_status(con, name, "active", f"used by {r['name']}")
    return {**r, "stages": stages}


def workflow_stages(con, name: str) -> list[dict]:
    row = get(con, name)
    if row["kind"] != "workflow":
        raise ReinsError(f"{name} is a {row['kind']}, not a workflow")
    return json.loads(row["body"])["stages"]


def diff(con, a: str, b: str) -> str:
    ra, rb = get(con, a), get(con, b)
    if ra["kind"] != rb["kind"] or ra["base"] != rb["base"]:
        raise ReinsError("diff two versions of the same artifact")
    ta = ra["body"] if ra["kind"] == "prompt" else json.dumps(json.loads(ra["body"]), indent=1, sort_keys=True)
    if ra["kind"] == "weights":                       # for weights the interesting diff is files + training, not hashes alone
        pass
    tb = rb["body"] if rb["kind"] == "prompt" else json.dumps(json.loads(rb["body"]), indent=1, sort_keys=True)
    return "".join(difflib.unified_diff(ta.splitlines(True), tb.splitlines(True), a, b))


def pinned_in(con, name: str) -> dict:
    """Module versions and workflows that pin this artifact."""
    ensure(con)
    mods = [r[0] for r in con.execute("SELECT version FROM module_version WHERE pins LIKE ?", (f'%"{name}"%',))]
    wfs = [r[0] for r in con.execute("SELECT name FROM artifact WHERE kind='workflow' AND body LIKE ?", (f'%"{name}"%',))]
    return {"module_versions": mods, "workflows": wfs}


def list_(con, kind: str | None = None) -> list[dict]:
    ensure(con)
    q = "SELECT name, kind, base, version, status, about, source, created FROM artifact"
    args = ()
    if kind:
        q += " WHERE kind=?"; args = (kind,)
    return [dict(r) for r in con.execute(q + " ORDER BY kind, base, version", args)]
