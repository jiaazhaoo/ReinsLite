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

KINDS = ("prompt", "model", "workflow")
NAME = re.compile(r"^(prompt|model|workflow)-([a-z][a-z0-9_]*)-v(\d+)$")

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
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifact'").fetchone():
        con.executescript(SCHEMA)


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
                       "models": [current[f"model:{m}"] for m in st.get("models", [])]})
    body = json.dumps({"stages": stages}, sort_keys=True)
    r = register(con, "workflow", base, body, wf.get("about"), "reins.toml", status="active")
    if r["new"]:
        for name in {a for s in stages for a in s["prompts"] + s["models"]}:
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
