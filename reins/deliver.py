"""C12 deliverables: contract check before anything leaves, then a versioned, read-only copy with a manifest.

    reins deliver check FILE --batch B [--key oachargeid] [--sheets 'Validation Summary,Polygon Validation']
                                      [--required col,col] [--allow-missing N]
    reins deliver register FILE --batch B [...same options...]    check, then copy as <stem>-v<N><ext> + .manifest.json, read-only

Checks (every one is a defect class that passed unit tests and still shipped):
  every batch case present exactly once (key column)  ·  no case that is not in the batch  ·  no CJK anywhere
  required columns non-empty  ·  sheet names exactly as declared  ·  no cell at the 254/255/32767 truncation lengths
  no column that is empty or constant across all rows
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from . import batches
from .store import ReinsError, now, tx

CJK = re.compile(r"[　-〿㐀-鿿豈-﫿＀-￯]")
TRUNC_LENGTHS = {254, 255, 32767}


def _sheets(path: Path) -> dict[str, list[list]]:
    suf = path.suffix.lower()
    if suf == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            raise ReinsError("openpyxl is needed to check xlsx") from None
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    if suf in (".csv", ".tsv"):
        with path.open(encoding="utf-8-sig", newline="") as f:
            return {path.stem: list(csv.reader(f, delimiter="\t" if suf == ".tsv" else ","))}
    raise ReinsError(f"{path}: only xlsx / csv / tsv are checked (geometry files: convert the attribute table to csv)")


def data_digest(sheets: dict[str, list[list]]) -> str:
    h = hashlib.sha256()
    for name in sorted(sheets):
        h.update(name.encode())
        for row in sheets[name]:
            h.update(json.dumps(["" if v is None else str(v) for v in row], ensure_ascii=False).encode())
    return h.hexdigest()


def check(con, path: Path, batch: str, key: str = "oachargeid", sheets_expected: list[str] | None = None,
          required: list[str] | None = None, allow_missing: int = 0) -> dict:
    batches.get(con, batch)
    members = {r[0] for r in con.execute("SELECT oachargeid FROM batch_case WHERE batch_id=?", (batch,))}
    sheets = _sheets(path)
    problems, n_rows = [], 0
    if sheets_expected is not None and list(sheets) != sheets_expected:
        problems.append(f"sheets are {list(sheets)}, expected {sheets_expected}")
    key_seen_anywhere = False
    for name, rows in sheets.items():
        if not rows:
            problems.append(f"[{name}] empty sheet"); continue
        header = ["" if h is None else str(h) for h in rows[0]]
        body = rows[1:]
        n_rows += len(body)
        for r_i, row in enumerate(rows, 1):
            for c_i, v in enumerate(row):
                if v is None:
                    continue
                s = str(v)
                if CJK.search(s):
                    problems.append(f"[{name}] row {r_i} col {header[c_i] if c_i < len(header) else c_i}: CJK text {s[:30]!r}")
                if len(s) in TRUNC_LENGTHS:
                    problems.append(f"[{name}] row {r_i} col {header[c_i] if c_i < len(header) else c_i}: length {len(s)} (truncated?)")
        if key in header:
            key_seen_anywhere = True
            ki = header.index(key)
            ids = ["" if r[ki] is None else str(r[ki]) for r in body if ki < len(r)]
            cnt = Counter(ids)
            dup = [k for k, n in cnt.items() if n > 1]
            if dup:
                problems.append(f"[{name}] {len(dup)} {key} values repeated (first: {dup[:5]})")
            extra = sorted(set(ids) - members - {""})
            if extra:
                problems.append(f"[{name}] {len(extra)} rows whose {key} is not in {batch} (first: {extra[:5]})")
            missing = sorted(members - set(ids))
            if len(missing) > allow_missing:
                problems.append(f"[{name}] {len(missing)} batch cases absent (first: {missing[:5]}); allowed {allow_missing}")
            if "" in cnt:
                problems.append(f"[{name}] {cnt['']} rows with empty {key}")
        for col in required or []:
            if col not in header:
                problems.append(f"[{name}] required column {col!r} missing"); continue
            ci = header.index(col)
            empty = sum(1 for r in body if ci >= len(r) or r[ci] in (None, ""))
            if empty:
                problems.append(f"[{name}] {col}: {empty} empty cells")
        for ci, col in enumerate(header):
            vals = {("" if r[ci] is None else str(r[ci])) for r in body if ci < len(r)}
            if body and len(vals) <= 1:
                problems.append(f"[{name}] column {col!r} is {'empty' if vals <= {''} else 'constant'} across all rows")
    if not key_seen_anywhere:
        problems.append(f"no sheet has a {key!r} column: C1 cannot be checked")
    return {"ok": not problems, "problems": problems, "n_rows": n_rows, "sheets": list(sheets),
            "data_digest": data_digest(sheets)}


def register(con, path: Path, batch: str, **check_kw) -> dict:
    res = check(con, path, batch, **check_kw)
    if not res["ok"]:
        raise ReinsError(f"{len(res['problems'])} contract problems; not registered:\n  " + "\n  ".join(res["problems"][:20]))
    b = batches.get(con, batch)
    with tx(con):
        v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM deliverable WHERE batch_id=?", (batch,)).fetchone()[0]
        dest_dir = Path(b["work_dir"]) / "delivery" if b["work_dir"] else path.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{path.stem}-v{v}{path.suffix}"
        if dest.exists():
            raise ReinsError(f"{dest} exists: deliverables are never overwritten")
        shutil.copy2(path, dest)
        from .names import sha256_file
        sha = sha256_file(dest)
        manifest = {"batch_id": batch, "version": v, "file": dest.name, "sha256": sha, "data_digest": res["data_digest"],
                    "n_rows": res["n_rows"], "sheets": res["sheets"], "release": b["release_name"], "ruleset": b["ruleset"],
                    "env": b["env_name"], "config_sha": b["config_sha"], "input_shas": json.loads(b["input_shas"]),
                    "n_cases": b["n_cases"], "case_set_sha": b["case_set_sha"], "registered": now()}
        dest.with_suffix(dest.suffix + ".manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
        subprocess.run(["chmod", "a-w", str(dest), str(dest.with_suffix(dest.suffix + ".manifest.json"))], check=False)
        con.execute("INSERT INTO deliverable (at, batch_id, version, path, sha256, data_digest, n_rows, manifest)"
                    " VALUES (?,?,?,?,?,?,?,?)", (now(), batch, v, str(dest), sha, res["data_digest"], res["n_rows"], json.dumps(manifest)))
        con.execute("INSERT INTO batch_event (at, batch_id, event, detail) VALUES (?,?,?,?)",
                    (now(), batch, "deliverable", f"v{v} {dest.name} rows={res['n_rows']}"))
    return {"version": v, "path": str(dest), "sha256": sha, "data_digest": res["data_digest"]}
