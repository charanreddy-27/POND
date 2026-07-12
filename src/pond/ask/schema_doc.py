"""Builds the schema description sent to the LLM.

Privacy contract (§6.2): only DDL-level structure and, when
``privacy.vocab_sharing = categorical``, low-cardinality vocabulary
(category names, activity types, account labels). Never row data,
never free text like narrations, messages, or search queries.
"""

from __future__ import annotations

import duckdb

from pond.config import Config

SCHEMA_DOC = """\
All tables live in DuckDB. Timestamps are TIMESTAMPTZ in the user's local
timezone. Amounts are DECIMAL(12,2) where NEGATIVE = money spent, positive =
money received.

CREATE TABLE transactions (
  ts TIMESTAMPTZ,          -- transaction date/time
  amount DECIMAL(12,2),    -- negative = spend, positive = credit
  currency VARCHAR,
  narration VARCHAR,       -- raw bank description
  merchant VARCHAR,        -- extracted, UPPERCASED (e.g. 'SWIGGY')
  category VARCHAR,        -- e.g. 'food_delivery' (see vocabulary below)
  account VARCHAR          -- e.g. 'hdfc_savings'
);

CREATE TABLE messages (
  ts TIMESTAMPTZ, chat VARCHAR,   -- chat/group name
  sender VARCHAR, is_me BOOLEAN,  -- is_me: sent by the user themself
  text VARCHAR,                   -- NULL for media messages
  is_media BOOLEAN, word_count INTEGER
);

CREATE TABLE listens (             -- Spotify listening history
  ts TIMESTAMPTZ,                  -- when playback ended
  track VARCHAR, artist VARCHAR, album VARCHAR,
  ms_played INTEGER,               -- filter ms_played >= 30000 for real listens
  skipped BOOLEAN                  -- NULL unless extended history
);

CREATE TABLE activities (          -- workouts / fitness sessions
  ts_start TIMESTAMPTZ, ts_end TIMESTAMPTZ,
  activity_type VARCHAR,           -- e.g. 'strength_training', 'badminton'
  duration_min DOUBLE, calories DOUBLE, steps INTEGER
);

CREATE TABLE daily_metrics (       -- one row per day from Google Fit
  day DATE, steps INTEGER, distance_m DOUBLE, calories DOUBLE, active_min DOUBLE
);

CREATE TABLE searches (ts TIMESTAMPTZ, query VARCHAR);          -- Google searches
CREATE TABLE youtube_watches (ts TIMESTAMPTZ, title VARCHAR, channel VARCHAR);
CREATE TABLE calendar_events (ts_start TIMESTAMPTZ, ts_end TIMESTAMPTZ,
                              title VARCHAR, all_day BOOLEAN);

-- Calendar spine views: absence has no rows, these manufacture the rows.
CREATE VIEW days AS  -- one row per day from earliest data to today
  SELECT day DATE, week_start DATE, month_start DATE;
CREATE VIEW weeks AS -- one row per ISO week
  SELECT week_start DATE,
         went_to_gym BOOLEAN;  -- TRUE if any gym-type activity that week

Conventions:
- "skipped gym" / "gym-free" style questions: use weeks.went_to_gym = FALSE,
  or LEFT JOIN days/weeks against the fact table and look for NULLs.
- "listened to" questions: add ms_played >= 30000.
- Spending is SUM(-amount) over amount < 0 rows.
- Every base table also has source/dedupe_key columns; ignore them.
"""

# Only these column/table pairs are ever sampled for vocabulary — all are
# closed categorical sets, not free text.
VOCAB_COLUMNS: list[tuple[str, str]] = [
    ("transactions", "category"),
    ("transactions", "account"),
    ("activities", "activity_type"),
]


def vocab_section(con: duckdb.DuckDBPyConnection) -> str:
    """Distinct values of the whitelisted categorical columns (max 50 each)."""
    lines = []
    for table, col in VOCAB_COLUMNS:
        vals = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 50"
            ).fetchall()
        ]
        if vals:
            lines.append(f"- {table}.{col}: {', '.join(vals)}")
    return "Known vocabulary (exact values in the data):\n" + "\n".join(lines) if lines else ""


def build_schema_doc(con: duckdb.DuckDBPyConnection, cfg: Config) -> str:
    """Full schema context for the system prompt, honouring privacy config."""
    doc = SCHEMA_DOC
    if cfg.privacy.vocab_sharing == "categorical":
        vocab = vocab_section(con)
        if vocab:
            doc = f"{doc}\n{vocab}\n"
    return doc
