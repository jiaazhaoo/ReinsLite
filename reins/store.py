"""The one registry every contract writes to: $REINS_HOME/reins.db (default /data/reins).

SQLite in WAL mode, so several sessions can read and write at once (the JSON board lost entries to concurrent
writers). Event tables are append-only; current state is either a column on the parent row or a view over events.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

DEFAULT_HOME = "/data/reins"
CODE_ROOT = Path("/env/code")

SCHEMA = """
CREATE TABLE IF NOT EXISTS module (
  name     TEXT PRIMARY KEY,
  project  TEXT NOT NULL,
  about    TEXT NOT NULL,
  created  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS module_version (
  version     TEXT PRIMARY KEY,                       -- <module>-<suffix>-<YYYYMMDD>-<n>
  module      TEXT NOT NULL REFERENCES module(name),
  suffix      TEXT NOT NULL,
  day         TEXT NOT NULL,                          -- YYYYMMDD
  seq         INTEGER NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('candidate', 'released', 'retired')),
  about       TEXT NOT NULL,
  commit_sha  TEXT,
  branch      TEXT,
  worktree    TEXT,
  session     TEXT,
  pins        TEXT NOT NULL DEFAULT '{}',             -- JSON: prompt sha256, model ids, params, asset sha256
  created     TEXT NOT NULL,
  UNIQUE (module, day, seq)
);
CREATE TABLE IF NOT EXISTS module_event (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  version  TEXT NOT NULL REFERENCES module_version(version),
  event    TEXT NOT NULL,
  detail   TEXT,
  session  TEXT
);
CREATE TABLE IF NOT EXISTS batch (
  batch_id       TEXT PRIMARY KEY,                    -- <council>-<wp>-<type>-<YYYYMMDD>-<n>
  project        TEXT NOT NULL,
  council        TEXT NOT NULL,
  wp             TEXT NOT NULL,
  type           TEXT NOT NULL,
  day            TEXT NOT NULL,
  seq            INTEGER NOT NULL,
  purpose        TEXT NOT NULL,
  parent         TEXT REFERENCES batch(batch_id),
  case_set_path  TEXT NOT NULL,
  case_set_sha   TEXT NOT NULL,
  n_cases        INTEGER NOT NULL,
  release_name   TEXT,
  aliases        TEXT NOT NULL DEFAULT '[]',          -- JSON list: what people call it ("第一批"); never used as a key
  status         TEXT NOT NULL CHECK (status IN ('open', 'running', 'paused', 'done', 'failed', 'closed')),
  mixed_version  INTEGER NOT NULL DEFAULT 0,
  spend_cap      REAL NOT NULL DEFAULT 0,             -- USD; 0 = paid stages blocked until approved
  owner_session  TEXT,
  created        TEXT NOT NULL,
  closed         TEXT,
  UNIQUE (council, wp, type, day, seq)
);
CREATE TABLE IF NOT EXISTS batch_case (
  batch_id    TEXT NOT NULL REFERENCES batch(batch_id),
  oachargeid  TEXT NOT NULL,
  PRIMARY KEY (batch_id, oachargeid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS batch_stage (
  batch_id        TEXT NOT NULL REFERENCES batch(batch_id),
  stage           TEXT NOT NULL,
  ord             INTEGER NOT NULL,
  module_version  TEXT REFERENCES module_version(version),
  status          TEXT NOT NULL CHECK (status IN ('planned', 'running', 'done', 'failed', 'skipped')),
  started         TEXT,
  ended           TEXT,
  output_path     TEXT,
  log_path        TEXT,
  note            TEXT,
  PRIMARY KEY (batch_id, stage)
);
CREATE TABLE IF NOT EXISTS batch_event (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  batch_id  TEXT NOT NULL REFERENCES batch(batch_id),
  event     TEXT NOT NULL,
  detail    TEXT,
  session   TEXT
);
CREATE TABLE IF NOT EXISTS case_event (                -- append-only; a retry adds a row, the latest row wins
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  at              TEXT NOT NULL,
  batch_id        TEXT NOT NULL,
  stage           TEXT NOT NULL,
  oachargeid      TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('done', 'skipped', 'failed')),
  reason          TEXT,
  module_version  TEXT,
  FOREIGN KEY (batch_id, oachargeid) REFERENCES batch_case(batch_id, oachargeid),
  FOREIGN KEY (batch_id, stage) REFERENCES batch_stage(batch_id, stage)
);
CREATE INDEX IF NOT EXISTS case_event_key ON case_event (batch_id, stage, oachargeid, id);
CREATE INDEX IF NOT EXISTS case_event_case ON case_event (oachargeid);
CREATE VIEW IF NOT EXISTS case_current AS
  SELECT e.* FROM case_event e
  WHERE e.id = (SELECT MAX(id) FROM case_event x
                WHERE x.batch_id = e.batch_id AND x.stage = e.stage AND x.oachargeid = e.oachargeid);
"""


class ReinsError(Exception):
    """A contract was broken; the message says which rule and what to do."""


def home() -> Path:
    h = Path(os.environ.get("REINS_HOME", DEFAULT_HOME)).resolve()
    if h == CODE_ROOT or CODE_ROOT in h.parents:
        raise ReinsError(f"REINS_HOME={h} is inside {CODE_ROOT}: no data in code folders; use /data/...")
    return h


def connect(path: Path | None = None) -> sqlite3.Connection:
    h = home() if path is None else path
    h.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(h / "reins.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con


class tx:
    """BEGIN IMMEDIATE ... COMMIT: takes the write lock up front, so allocating the next -<n> cannot race."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self) -> sqlite3.Connection:
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, *_):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().strftime("%Y%m%d")


def session() -> str | None:
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("REINS_SESSION")
