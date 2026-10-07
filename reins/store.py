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
  ruleset        TEXT,                                -- ruleset-<project>-vN the lanes were computed with
  env_name       TEXT,                                -- env-<name>-vN the stages ran in
  config_sha     TEXT,                                -- sha256 of the config file(s) the run read
  input_shas     TEXT NOT NULL DEFAULT '{}',          -- JSON {path: sha256} of mapping tables / other inputs
  preflight_ok   INTEGER NOT NULL DEFAULT 0,
  probe_cmd      TEXT,                                -- project command printing human progress ({work_dir} substituted)
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
-- C4 benchmarks, thresholds; rulesets; environments
CREATE TABLE IF NOT EXISTS benchmark (
  name      TEXT NOT NULL,                            -- bench-<name>
  version   INTEGER NOT NULL,
  project   TEXT NOT NULL,
  path      TEXT NOT NULL,
  status    TEXT NOT NULL CHECK (status IN ('open', 'frozen')),
  n_cases   INTEGER NOT NULL,
  n_dev     INTEGER NOT NULL,
  n_holdout INTEGER NOT NULL,
  labelset  INTEGER NOT NULL DEFAULT 0,               -- bumps on every label append
  holdout_compromised INTEGER NOT NULL DEFAULT 0,     -- tuned on holdout: it is dev now
  manifest_sha TEXT,
  created   TEXT NOT NULL,
  frozen    TEXT,
  PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS benchmark_event (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  name     TEXT NOT NULL,
  version  INTEGER NOT NULL,
  event    TEXT NOT NULL,                             -- created | stage_added | labels_added | frozen | holdout_access | holdout_tuned | bumped
  detail   TEXT,
  session  TEXT
);
CREATE TABLE IF NOT EXISTS threshold (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  module    TEXT NOT NULL,
  name      TEXT NOT NULL,
  value     TEXT NOT NULL,
  n         INTEGER NOT NULL,
  split     TEXT NOT NULL CHECK (split IN ('dev', 'holdout', 'other')),
  benchmark TEXT,
  low_n     INTEGER NOT NULL,
  by_whom   TEXT,
  note      TEXT
);
CREATE TABLE IF NOT EXISTS ruleset (
  name      TEXT PRIMARY KEY,                         -- ruleset-<project>-vN
  project   TEXT NOT NULL,
  version   INTEGER NOT NULL,
  sha       TEXT NOT NULL,
  path      TEXT NOT NULL,
  n_rules   INTEGER NOT NULL,
  diff_path TEXT,                                     -- lane diff that justified it (required from v2 on)
  diff_summary TEXT,
  created   TEXT NOT NULL,
  session   TEXT
);
CREATE TABLE IF NOT EXISTS env (
  name      TEXT PRIMARY KEY,                         -- env-<name>-vN
  base      TEXT NOT NULL,
  version   INTEGER NOT NULL,
  sha       TEXT NOT NULL,
  manifest  TEXT NOT NULL,                            -- JSON: python, packages, cuda, driver, assets {path: sha}
  created   TEXT NOT NULL
);
-- C6 review records (generic; the project supplies the error taxonomy in review.toml)
CREATE TABLE IF NOT EXISTS review_event (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  batch_id    TEXT NOT NULL,
  oachargeid  TEXT NOT NULL,
  target      TEXT NOT NULL DEFAULT 'case',           -- which output of the case (polygon, address, date, ...)
  reviewer    TEXT NOT NULL,
  action      TEXT NOT NULL CHECK (action IN ('view', 'verdict', 'draw', 'note')),
  verdict     TEXT CHECK (verdict IS NULL OR verdict IN ('correct', 'wrong', 'unsure')),
  error_type  TEXT,
  note        TEXT,
  payload     TEXT,                                   -- JSON (drawn geometry, evidence refs)
  taxonomy_version INTEGER,
  client      TEXT
);
CREATE INDEX IF NOT EXISTS review_case ON review_event (batch_id, oachargeid, id);
CREATE TABLE IF NOT EXISTS review_assignment (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  batch_id   TEXT NOT NULL,
  oachargeid TEXT NOT NULL,
  reviewer   TEXT NOT NULL,
  queue      TEXT,
  round      TEXT,
  ord        INTEGER NOT NULL,
  by_whom    TEXT
);
-- C11 acceptance
CREATE TABLE IF NOT EXISTS acceptance (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  batch_id    TEXT NOT NULL,
  seed        INTEGER NOT NULL,
  n           INTEGER NOT NULL,
  from_lane   TEXT NOT NULL,
  lanes_sha   TEXT NOT NULL,
  sample      TEXT NOT NULL,                          -- JSON [{oachargeid, lane}]
  budget_ideal INTEGER NOT NULL,
  budget_max  INTEGER NOT NULL,
  decision    TEXT CHECK (decision IS NULL OR decision IN ('ship', 'ship_with_note', 'rework')),
  decided_at  TEXT,
  decided_by  TEXT,
  note        TEXT
);
CREATE TABLE IF NOT EXISTS acceptance_grade (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  acceptance_id INTEGER NOT NULL REFERENCES acceptance(id),
  oachargeid  TEXT NOT NULL,
  grade       TEXT NOT NULL CHECK (grade IN ('P', 'A', 'F')),
  by_whom     TEXT NOT NULL,
  note        TEXT
);
-- preflight and deliverables
CREATE TABLE IF NOT EXISTS preflight (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  batch_id  TEXT NOT NULL,
  check_name TEXT NOT NULL,
  ok        INTEGER NOT NULL,
  detail    TEXT
);
CREATE TABLE IF NOT EXISTS deliverable (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        TEXT NOT NULL,
  batch_id  TEXT NOT NULL,
  version   INTEGER NOT NULL,
  path      TEXT NOT NULL,
  sha256    TEXT NOT NULL,
  data_digest TEXT NOT NULL,                          -- hash of the rows, not the container (xlsx zips differ per build)
  n_rows    INTEGER NOT NULL,
  manifest  TEXT NOT NULL                             -- JSON provenance tuple
);
-- C15 sessions: every Claude Code session, what it is for, what it did
CREATE TABLE IF NOT EXISTS session (
  id            TEXT PRIMARY KEY,                     -- Claude Code session id
  started       TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  ended         TEXT,
  status        TEXT NOT NULL CHECK (status IN ('active', 'idle', 'ended')),
  cwd           TEXT,
  transcript    TEXT,
  prefix_sha    TEXT,                                 -- hash of the transcript's first lines: forks share it
  parent        TEXT,                                 -- the session this one was forked from (inferred)
  role          TEXT CHECK (role IS NULL OR role IN ('develop', 'run', 'experiment', 'analysis')),
  purpose       TEXT,                                 -- one line, from the session (reins session bind) or inferred
  module        TEXT,                                 -- the sub-module it updates
  version       TEXT,                                 -- the module version it develops (registered)
  batch_id      TEXT,                                 -- the batch it runs
  project       TEXT,
  n_edits       INTEGER NOT NULL DEFAULT 0,
  n_commands    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS session_event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  session_id TEXT NOT NULL,
  kind       TEXT NOT NULL,      -- start | resume | edit | run_start | run_stop | experiment | git | merge | dev | blocked | command | bind | end
  repo       TEXT,
  worktree   TEXT,
  path       TEXT,
  module     TEXT,
  batch_id   TEXT,
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS session_event_s ON session_event (session_id, id);
CREATE INDEX IF NOT EXISTS session_event_wt ON session_event (worktree, at);
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
    defaults = {"gateway_port": 8790, "board_port": 8791, "bench_root": "/data/benchmarks", "notify_cmd": "", "stall_minutes": 30,
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
    _migrate(con)
    return con


MIGRATIONS = [("batch", "probe_cmd", "TEXT"), ("batch", "time_budget_h", "REAL")]


def _migrate(con: sqlite3.Connection) -> None:
    """Columns added after a registry was created (CREATE TABLE IF NOT EXISTS does not add them)."""
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


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
