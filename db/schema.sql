-- WC Engine — local SQLite schema. No server, single file (wc.db).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teams (
  id            TEXT PRIMARY KEY,            -- slug, e.g. 'ivory-coast'
  name          TEXT NOT NULL,               -- 'Ivory Coast'  (always full names)
  confederation TEXT NOT NULL,               -- CONMEBOL|UEFA|CAF|AFC|CONCACAF|OFC
  group_code    TEXT,
  created_at    TEXT DEFAULT (datetime('now'))
);

-- Model inputs per team (the Form_L20 row, normalized).
CREATE TABLE IF NOT EXISTS team_form (
  team_id    TEXT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
  played     INTEGER NOT NULL,
  wins       INTEGER NOT NULL,
  draws      INTEGER NOT NULL,
  losses     INTEGER NOT NULL,
  gf         INTEGER NOT NULL,
  ga         INTEGER NOT NULL,
  pass_acc   REAL NOT NULL DEFAULT 80,
  pressing   REAL NOT NULL DEFAULT 6,
  sos        REAL NOT NULL,                  -- evidence-based strength-of-schedule
  notes      TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

-- Fixtures + results.
CREATE TABLE IF NOT EXISTS matches (
  id          TEXT PRIMARY KEY,
  stage       TEXT NOT NULL,                 -- group|r32|r16|qf|sf|final
  group_code  TEXT,
  kickoff     TEXT,
  home_id     TEXT REFERENCES teams(id),
  away_id     TEXT REFERENCES teams(id),
  home_goals  INTEGER,                       -- NULL until played
  away_goals  INTEGER,
  status      TEXT NOT NULL DEFAULT 'scheduled', -- scheduled|live|final
  source      TEXT,
  updated_at  TEXT DEFAULT (datetime('now'))
);

-- Versioned model parameters. Tuner proposes; you approve.
CREATE TABLE IF NOT EXISTS model_params (
  version    INTEGER PRIMARY KEY,
  params     TEXT NOT NULL,                  -- JSON
  brier      REAL,
  log_loss   REAL,
  note       TEXT,
  approved   INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

-- Power ratings. prior_power = pre-tournament; power folds in WC results.
CREATE TABLE IF NOT EXISTS power_ratings (
  team_id        TEXT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
  power          REAL NOT NULL,
  prior_power    REAL,
  wc_games       INTEGER DEFAULT 0,
  params_version INTEGER,
  computed_at    TEXT DEFAULT (datetime('now'))
);

-- Squad / availability (squad monitor writes here; power_adjustment feeds engine).
CREATE TABLE IF NOT EXISTS squad_status (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id          TEXT REFERENCES teams(id) ON DELETE CASCADE,
  player           TEXT NOT NULL,
  status           TEXT NOT NULL,            -- out|doubt|available
  importance       REAL,
  power_adjustment REAL DEFAULT 0,
  source           TEXT,
  reported_at      TEXT DEFAULT (datetime('now'))
);

-- Engine output per match.
CREATE TABLE IF NOT EXISTS predictions (
  match_id        TEXT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
  win_home        REAL,
  draw            REAL,
  win_away        REAL,
  pred_home_goals REAL,
  pred_away_goals REAL,
  params_version  INTEGER,
  computed_at     TEXT DEFAULT (datetime('now'))
);

-- Audit log. Every agent action lands here as a proposal first (guardrail).
CREATE TABLE IF NOT EXISTS agent_runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent      TEXT NOT NULL,
  action     TEXT NOT NULL,
  payload    TEXT,                           -- JSON
  status     TEXT NOT NULL DEFAULT 'proposed', -- proposed|applied|rejected
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_matches_status  ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff);
CREATE INDEX IF NOT EXISTS idx_agent_runs      ON agent_runs(agent, created_at DESC);
