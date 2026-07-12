"""DuckDB connection factory, schema migrations, and the calendar spine views."""

from __future__ import annotations

from pathlib import Path

import duckdb

from pond.config import Config

# Curated schema (§3.2 of the spec). Comments here are mirrored into the
# LLM-facing schema doc in ask/schema_doc.py.
CURATED_TABLES: dict[str, str] = {
    "transactions": """
        CREATE TABLE IF NOT EXISTS transactions (
          ts            TIMESTAMPTZ NOT NULL,
          amount        DECIMAL(12,2) NOT NULL,
          currency      VARCHAR DEFAULT 'INR',
          narration     VARCHAR,
          merchant      VARCHAR,
          category      VARCHAR,
          account       VARCHAR,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "messages": """
        CREATE TABLE IF NOT EXISTS messages (
          ts            TIMESTAMPTZ NOT NULL,
          chat          VARCHAR NOT NULL,
          sender        VARCHAR NOT NULL,
          is_me         BOOLEAN NOT NULL,
          text          VARCHAR,
          is_media      BOOLEAN DEFAULT FALSE,
          word_count    INTEGER,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "listens": """
        CREATE TABLE IF NOT EXISTS listens (
          ts            TIMESTAMPTZ NOT NULL,
          track         VARCHAR,
          artist        VARCHAR,
          album         VARCHAR,
          ms_played     INTEGER,
          skipped       BOOLEAN,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "activities": """
        CREATE TABLE IF NOT EXISTS activities (
          ts_start      TIMESTAMPTZ NOT NULL,
          ts_end        TIMESTAMPTZ,
          activity_type VARCHAR,
          duration_min  DOUBLE,
          calories      DOUBLE,
          steps         INTEGER,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "daily_metrics": """
        CREATE TABLE IF NOT EXISTS daily_metrics (
          day           DATE NOT NULL,
          steps         INTEGER,
          distance_m    DOUBLE,
          calories      DOUBLE,
          active_min    DOUBLE,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "searches": """
        CREATE TABLE IF NOT EXISTS searches (
          ts            TIMESTAMPTZ NOT NULL,
          query         VARCHAR NOT NULL,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "youtube_watches": """
        CREATE TABLE IF NOT EXISTS youtube_watches (
          ts            TIMESTAMPTZ NOT NULL,
          title         VARCHAR,
          channel       VARCHAR,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
    "calendar_events": """
        CREATE TABLE IF NOT EXISTS calendar_events (
          ts_start      TIMESTAMPTZ NOT NULL,
          ts_end        TIMESTAMPTZ,
          title         VARCHAR,
          all_day       BOOLEAN DEFAULT FALSE,
          source        VARCHAR NOT NULL,
          dedupe_key    VARCHAR NOT NULL UNIQUE
        )""",
}

LEDGER_TABLE = """
    CREATE TABLE IF NOT EXISTS _imports (
      file_sha256   VARCHAR PRIMARY KEY,
      path          VARCHAR,
      source        VARCHAR,
      imported_at   TIMESTAMPTZ DEFAULT now(),
      rows_inserted BIGINT
    )"""

# Calendar spine (§3.3): absence has no rows, so we manufacture the rows.
SPINE_VIEWS = """
    CREATE OR REPLACE VIEW days AS
    SELECT CAST(d AS DATE) AS day,
           date_trunc('week', d)::DATE AS week_start,
           date_trunc('month', d)::DATE AS month_start
    FROM generate_series(
           (SELECT LEAST(COALESCE(min(ts::DATE), CURRENT_DATE),
                         CURRENT_DATE - INTERVAL 365 DAY)::DATE
              FROM (SELECT ts FROM transactions UNION ALL SELECT ts FROM listens
                    UNION ALL SELECT ts FROM messages)),
           CURRENT_DATE, INTERVAL 1 DAY) t(d);

    CREATE OR REPLACE VIEW weeks AS
    SELECT week_start,
           EXISTS (SELECT 1 FROM activities a
                   WHERE date_trunc('week', a.ts_start)::DATE = w.week_start
                     AND a.activity_type IN ('strength_training','gym','weightlifting'))
             AS went_to_gym
    FROM (SELECT DISTINCT week_start FROM days) w;
"""


def connect(cfg: Config, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the pond database and run migrations.

    Sets the session timezone from config so TIMESTAMPTZ values render in
    local time, and (re)creates the spine views on every connect — they are
    cheap and this keeps them current with the code.
    """
    path = cfg.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute(f"SET TimeZone = '{cfg.me.timezone}'")
    if not read_only:
        migrate(con)
    return con


def migrate(con: duckdb.DuckDBPyConnection) -> None:
    """Create curated tables, the import ledger, and spine views (idempotent)."""
    for ddl in CURATED_TABLES.values():
        con.execute(ddl)
    con.execute(LEDGER_TABLE)
    con.execute(SPINE_VIEWS)


def table_summary(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int, str | None, str | None]]:
    """Per curated table: (name, row_count, min_date, max_date) for `pond status`."""
    out: list[tuple[str, int, str | None, str | None]] = []
    ts_col = {
        "transactions": "ts",
        "messages": "ts",
        "listens": "ts",
        "activities": "ts_start",
        "daily_metrics": "day",
        "searches": "ts",
        "youtube_watches": "ts",
        "calendar_events": "ts_start",
    }
    for table, col in ts_col.items():
        n, lo, hi = con.execute(
            f"SELECT count(*), min({col})::DATE::VARCHAR, max({col})::DATE::VARCHAR FROM {table}"
        ).fetchone()
        out.append((table, n, lo, hi))
    return out


def date_range(
    con: duckdb.DuckDBPyConnection, table: str, col: str
) -> tuple[str | None, str | None]:
    """(min, max) dates of ``col`` in ``table`` as ISO strings, for import summaries."""
    lo, hi = con.execute(
        f"SELECT min({col})::DATE::VARCHAR, max({col})::DATE::VARCHAR FROM {table}"
    ).fetchone()
    return lo, hi


def db_exists(cfg: Config) -> bool:
    """True if the DuckDB file exists on disk."""
    return Path(cfg.db_path).exists()
