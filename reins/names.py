"""Name formats (C2, C3) and case-id checks (C1). Pure functions, no registry access."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import re
from collections import Counter
from pathlib import Path

from .store import ReinsError

TOKEN = r"[a-z][a-z0-9_]*"
BATCH_TYPES = ("production", "rework", "experiment", "pilot", "eval", "benchmark_build", "smoke", "drift")

MODULE_VERSION = re.compile(rf"^(?P<module>{TOKEN})-(?P<suffix>[a-z0-9][a-z0-9_]*)-(?P<day>\d{{8}})-(?P<seq>[1-9]\d*)$")
BATCH_ID = re.compile(rf"^(?P<council>{TOKEN})-(?P<wp>{TOKEN})-(?P<type>{'|'.join(BATCH_TYPES)})"
                      rf"-(?P<day>\d{{8}})-(?P<seq>[1-9]\d*)$")
TOKEN_RE = re.compile(rf"^{TOKEN}$")

CASE_KEY = "oachargeid"
DEFAULT_CASE_PATTERN = r"^[0-9]+$"
# header words a case list has been seen to carry into the id column (a downloader once read the header as a case)
HEADER_WORDS = {"oachargeid", "charge_id", "chargeid", "case_id", "id", "originating-authority-charge-identifier"}


def check_token(kind: str, value: str) -> str:
    if not TOKEN_RE.match(value):
        raise ReinsError(f"{kind} {value!r}: lowercase letters, digits and _ only, starting with a letter "
                         f"('-' separates the parts of a name)")
    return value


def check_day(day: str) -> str:
    try:
        dt.datetime.strptime(day, "%Y%m%d")
    except ValueError:
        raise ReinsError(f"day {day!r} is not a YYYYMMDD date") from None
    return day


def module_version(module: str, suffix: str, day: str, seq: int) -> str:
    check_token("module", module)
    if not re.match(r"^[a-z0-9][a-z0-9_]*$", suffix):
        raise ReinsError(f"suffix {suffix!r}: lowercase letters, digits and _ only")
    name = f"{module}-{suffix}-{check_day(day)}-{seq}"
    assert MODULE_VERSION.match(name), name
    return name


def parse_module_version(name: str) -> dict:
    m = MODULE_VERSION.match(name)
    if not m:
        raise ReinsError(f"module version {name!r} is not <module>-<suffix>-<YYYYMMDD>-<n> (n from 1)")
    d = m.groupdict()
    check_day(d["day"])
    d["seq"] = int(d["seq"])
    return d


def batch_id(council: str, wp: str, type_: str, day: str, seq: int) -> str:
    check_token("council", council)
    check_token("wp", wp)
    if type_ not in BATCH_TYPES:
        raise ReinsError(f"batch type {type_!r} is not one of {', '.join(BATCH_TYPES)}")
    name = f"{council}-{wp}-{type_}-{check_day(day)}-{seq}"
    assert BATCH_ID.match(name), name
    return name


def parse_batch_id(name: str) -> dict:
    m = BATCH_ID.match(name)
    if not m:
        raise ReinsError(f"batch id {name!r} is not <council>-<wp>-<type>-<YYYYMMDD>-<n>; "
                         f"type is one of {', '.join(BATCH_TYPES)}")
    d = m.groupdict()
    d["seq"] = int(d["seq"])
    return d


def case_problems(ids: list[str], pattern: str = DEFAULT_CASE_PATTERN) -> list[str]:
    """Every reason this list cannot be a batch's case set. Empty list = fine."""
    rx = re.compile(pattern)
    out = []
    for i, v in enumerate(ids, 1):
        if v != v.strip():
            out.append(f"row {i}: {v!r} has surrounding whitespace")
        elif not v:
            out.append(f"row {i}: empty oachargeid")
        elif v.lower() in HEADER_WORDS:
            out.append(f"row {i}: {v!r} is a header word, not a case")
        elif not rx.match(v):
            out.append(f"row {i}: {v!r} does not match the project's oachargeid pattern {pattern}")
    for v, n in Counter(ids).items():
        if n > 1:
            out.append(f"{v!r} appears {n} times")
    return out


def read_case_set(path: Path, column: str = CASE_KEY) -> list[str]:
    """A .csv/.tsv with an `oachargeid` column, or a plain list (one id per line, no header)."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".csv", ".tsv"):
        rows = list(csv.DictReader(text.splitlines(), delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
        if not rows or column not in rows[0]:
            raise ReinsError(f"{path}: no {column!r} column (columns: {list(rows[0]) if rows else 'none'}); "
                             f"the case key is always oachargeid -- rename the column at ingest")
        return [r[column] for r in rows]
    return [ln for ln in text.splitlines() if ln != ""]


def sha256_lines(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
