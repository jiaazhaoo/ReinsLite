"""The one registry every contract writes to: $REINS_HOME/reins.db (default /data/reins).

SQLite in WAL mode, so several sessions can read and write at once (the JSON board lost entries to concurrent
writers). Event tables are append-only; current state is either a column on the parent row or a view over events.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tomllib
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
  status      TEXT NOT NULL CHECK (status IN ('candidate', 'released', 'retired', 'abandoned')),
  about       TEXT NOT NULL,
  commit_sha  TEXT,
  branch      TEXT,
  worktree    TEXT,
  session     TEXT,
  pins        TEXT NOT NULL DEFAULT '{}',             -- JSON: prompt sha256, model ids, params, asset sha256,
                                                      --       from_batch, pilot_cases
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
CREATE TABLE IF NOT EXISTS release (
  name      TEXT PRIMARY KEY,                         -- rel-<project>-<YYYYMMDD>-<n>
  project   TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  worktree  TEXT NOT NULL,
  note      TEXT,
  versions  TEXT NOT NULL DEFAULT '[]',               -- JSON list of module versions it contains
  created   TEXT NOT NULL,
  session   TEXT
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
  status_reason  TEXT,
  mixed_version  INTEGER NOT NULL DEFAULT 0,
  spend_cap      REAL NOT NULL DEFAULT 0,             -- USD; 0 = paid stages blocked until approved
  work_dir       TEXT,                                -- the batch's own output directory
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
  paid            INTEGER NOT NULL DEFAULT 0,         -- calls paid models (needs spend approval)
  spend_cap       REAL,                               -- USD, optional, within the batch cap
  time_limit_s    INTEGER,                            -- per case; the watchdog reports cases past it
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
  status          TEXT NOT NULL CHECK (status IN ('started', 'done', 'skipped', 'failed')),
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

-- C10 leases: one holder per resource
CREATE TABLE IF NOT EXISTS lease (
  resource  TEXT PRIMARY KEY,                         -- worktree:<path> | batch:<id> | gpu:0 | port:8771
  holder    TEXT NOT NULL,                            -- session id
  purpose   TEXT,
  since     TEXT NOT NULL
);
-- C7/C10 processes the runner started
CREATE TABLE IF NOT EXISTS process (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id  TEXT NOT NULL REFERENCES batch(batch_id),
  stage     TEXT NOT NULL,
  pgid      INTEGER NOT NULL,
  supervisor_pid INTEGER NOT NULL,
  cmd       TEXT NOT NULL,
  cwd       TEXT NOT NULL,
  log_path  TEXT NOT NULL,
  session   TEXT,
  state     TEXT NOT NULL CHECK (state IN ('running', 'paused', 'exited', 'stopped', 'lost')),
  attempt   INTEGER NOT NULL DEFAULT 1,
  exit_code INTEGER,
  started   TEXT NOT NULL,
  ended     TEXT
);
-- C9 spend ledger: reserve before the call, settle after
CREATE TABLE IF NOT EXISTS spend_reserve (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  batch_id  TEXT NOT NULL,
  stage     TEXT NOT NULL,
  amount    REAL NOT NULL,
  state     TEXT NOT NULL CHECK (state IN ('open', 'settled', 'released'))
);
CREATE TABLE IF NOT EXISTS spend (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  batch_id    TEXT NOT NULL,
  stage       TEXT NOT NULL,
  module_version TEXT,
  provider    TEXT NOT NULL,
  model       TEXT NOT NULL,
  amount      REAL NOT NULL,                          -- USD actually charged (0 on a cache hit)
  priced      TEXT NOT NULL,                          -- provider | table | default
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cache_hit   INTEGER NOT NULL DEFAULT 0,
  request_sha TEXT,
  reserve_id  INTEGER REFERENCES spend_reserve(id),
  http_status INTEGER
);
CREATE INDEX IF NOT EXISTS spend_batch ON spend (batch_id, stage);
CREATE TABLE IF NOT EXISTS price (
  model         TEXT NOT NULL,
  input_per_m   REAL NOT NULL,
  output_per_m  REAL NOT NULL,
  since         TEXT NOT NULL,
  source        TEXT,
  PRIMARY KEY (model, since)
);
-- C5 gate results
CREATE TABLE IF NOT EXISTS gate_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  at             TEXT NOT NULL,
  version        TEXT NOT NULL REFERENCES module_version(version),
  benchmark      TEXT NOT NULL,
  tiers          TEXT NOT NULL,                       -- comma list actually run
  stages_covered TEXT NOT NULL,
  missed_error   INTEGER NOT NULL,
  review_load    INTEGER NOT NULL,
  base_missed_error INTEGER NOT NULL,
  base_review_load  INTEGER NOT NULL,
  golden_regressions INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL CHECK (status IN ('green', 'red')),
  diff_path      TEXT,
  skipped        TEXT,                                -- tiers skipped and why
  session        TEXT
);
-- C13 decisions
CREATE TABLE IF NOT EXISTS decision (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  at            TEXT NOT NULL,
  project       TEXT,
  text          TEXT NOT NULL,
  evidence      TEXT NOT NULL,
  by_whom       TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
  superseded_by INTEGER REFERENCES decision(id),
  session       TEXT
);
-- C8 notifications sent
CREATE TABLE IF NOT EXISTS notification (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  at     TEXT NOT NULL,
  key    TEXT NOT NULL,                               -- dedupe key
  level  TEXT NOT NULL,                               -- info | warn | action
  title  TEXT NOT NULL,
  body   TEXT,
  acked  INTEGER NOT NULL DEFAULT 0
);
"""


class ReinsError(Exception):
    """A contract was broken; the message says which rule and what to do."""


def home() -> Path:
    h = Path(os.environ.get("REINS_HOME", DEFAULT_HOME)).resolve()
    if h == CODE_ROOT or CODE_ROOT in h.parents:
        raise ReinsError(f"REINS_HOME={h} is inside {CODE_ROOT}: no data in code folders; use /data/...")
    return h


def config() -> dict:
    """$REINS_HOME/config.toml: gateway port, notify command, thresholds. All optional."""
    f = home() / "config.toml"
    cfg = tomllib.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    defaults = {"gateway_port": 8790, "board_port": 8791, "notify_cmd": "", "stall_minutes": 30,
                "disk_pause_pct": 90, "mem_pause_pct": 95, "gpu_warn_pct": 90, "default_call_estimate": 0.05,
                "spend_warn_fraction": 0.8}
    return {**defaults, **cfg}


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
    return os.environ.get("REINS_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID")   # explicit override first
