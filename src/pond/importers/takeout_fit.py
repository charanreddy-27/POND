"""Takeout sub-importer: Google Fit dailies + sessions (§5.3)."""

from __future__ import annotations

import json
import re
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from dateutil import parser as dateparser

from pond.config import Config
from pond.importers.base import ImportStats, insert_dedupe
from pond.ledger import already_imported, file_sha256, record_import, row_key

DAILIES_REL = Path("Fit") / "Daily activity metrics" / "Daily activity metrics.csv"
SESSIONS_REL = Path("Fit") / "All Sessions"

DAILY_COLS = ["day", "steps", "distance_m", "calories", "active_min", "source", "dedupe_key"]
ACTIVITY_COLS = [
    "ts_start", "ts_end", "activity_type", "duration_min", "calories", "steps",
    "source", "dedupe_key",
]

# Fuzzy header matching: column names vary by locale/version. First hit wins.
HEADER_MAP: list[tuple[str, str]] = [
    ("date", "day"),
    ("step", "steps"),
    ("distance", "distance_m"),
    ("calorie", "calories"),
    ("move minutes", "active_min"),
    ("active", "active_min"),
]


def run(
    root: Path, con: duckdb.DuckDBPyConnection, cfg: Config, force: bool = False
) -> ImportStats | None:
    """Import Fit dailies and sessions; None if no Fit folder at all."""
    dailies = root / DAILIES_REL
    sessions_dir = root / SESSIONS_REL
    if not dailies.exists() and not sessions_dir.is_dir():
        return None
    stats = ImportStats()
    if dailies.exists():
        _load_dailies(dailies, con, stats, force)
    if sessions_dir.is_dir():
        _load_sessions(sessions_dir, con, cfg, stats, force)
    return stats


def _load_dailies(
    f: Path, con: duckdb.DuckDBPyConnection, stats: ImportStats, force: bool
) -> None:
    stats.tables.add("daily_metrics")
    sha = file_sha256(f)
    if not force and already_imported(con, sha):
        stats.files_skipped += 1
        return

    con.execute(
        "CREATE OR REPLACE TEMP TABLE _stg_fit_daily AS SELECT * FROM read_csv_auto(?)",
        [str(f)],
    )
    headers = [r[0] for r in con.execute("DESCRIBE _stg_fit_daily").fetchall()]
    mapping: dict[str, str] = {}  # target column -> source header
    for header in headers:
        for needle, target in HEADER_MAP:
            if needle in header.lower() and target not in mapping:
                mapping[target] = header
                break
        else:
            stats.warnings.append(f"Fit dailies: ignoring unknown column {header!r}")
    if "day" not in mapping:
        stats.warnings.append("Fit dailies: no date column recognized; file skipped")
        return

    def col(target: str, cast: str) -> str:
        src = mapping.get(target)
        return f'TRY_CAST("{src}" AS {cast}) AS {target}' if src else f"NULL::{cast} AS {target}"

    select = (
        f"SELECT TRY_CAST(\"{mapping['day']}\" AS DATE) AS day, "
        f"{col('steps', 'INTEGER')}, {col('distance_m', 'DOUBLE')}, "
        f"{col('calories', 'DOUBLE')}, {col('active_min', 'DOUBLE')}, "
        "'google_fit' AS source, "
        "sha256(concat_ws('|', 'google_fit', "
        f"  TRY_CAST(\"{mapping['day']}\" AS DATE)::VARCHAR)) AS dedupe_key "
        f"FROM _stg_fit_daily WHERE TRY_CAST(\"{mapping['day']}\" AS DATE) IS NOT NULL"
    )
    ins, skip = insert_dedupe(con, "daily_metrics", DAILY_COLS, select)
    stats.rows_inserted += ins
    stats.rows_skipped += skip
    record_import(con, sha, f, "takeout", ins)


_DURATION_RE = re.compile(r"([\d.]+)\s*s", re.IGNORECASE)


def _parse_duration_min(raw: object) -> float | None:
    """'2700.5s' -> 45.0 minutes; bare numbers are treated as seconds."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw) / 60.0
    m = _DURATION_RE.search(str(raw))
    return float(m.group(1)) / 60.0 if m else None


def _normalize_activity(raw: str | None) -> str | None:
    """'Strength training' -> 'strength_training'."""
    if not raw:
        return None
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def _load_sessions(
    d: Path, con: duckdb.DuckDBPyConnection, cfg: Config, stats: ImportStats, force: bool
) -> None:
    stats.tables.add("activities")
    tz = ZoneInfo(cfg.me.timezone)
    for f in sorted(d.glob("*.json")):
        sha = file_sha256(f)
        if not force and already_imported(con, sha):
            stats.files_skipped += 1
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            stats.warnings.append(f"Fit session {f.name}: unreadable ({e})")
            continue
        sessions = data if isinstance(data, list) else [data]
        rows = []
        for s in sessions:
            if not isinstance(s, dict) or "startTime" not in s:
                continue
            try:
                ts_start = dateparser.parse(s["startTime"])
                ts_end = dateparser.parse(s["endTime"]) if s.get("endTime") else None
            except (ValueError, TypeError):
                stats.warnings.append(f"Fit session {f.name}: bad timestamps, skipped")
                continue
            if ts_start.tzinfo is None:
                ts_start = ts_start.replace(tzinfo=tz)
            if ts_end is not None and ts_end.tzinfo is None:
                ts_end = ts_end.replace(tzinfo=tz)
            activity = _normalize_activity(s.get("fitnessActivity"))
            duration = _parse_duration_min(s.get("duration"))
            if duration is None and ts_end is not None:
                duration = (ts_end - ts_start).total_seconds() / 60.0
            calories = None
            for agg in s.get("aggregate", []) or []:
                if "calories" in str(agg.get("metricName", "")).lower():
                    calories = agg.get("floatValue")
                    break
            rows.append((
                ts_start, ts_end, activity, duration, calories, None, "google_fit",
                row_key(
                    "google_fit",
                    ts_start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    activity,
                ),
            ))
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _stg_fit_sessions "
            "(ts_start TIMESTAMPTZ, ts_end TIMESTAMPTZ, activity_type VARCHAR, "
            " duration_min DOUBLE, calories DOUBLE, steps INTEGER, source VARCHAR, "
            " dedupe_key VARCHAR)"
        )
        if rows:
            con.executemany("INSERT INTO _stg_fit_sessions VALUES (?,?,?,?,?,?,?,?)", rows)
        ins, skip = insert_dedupe(
            con, "activities", ACTIVITY_COLS, "SELECT * FROM _stg_fit_sessions"
        )
        stats.rows_inserted += ins
        stats.rows_skipped += skip
        record_import(con, sha, f, "takeout", ins)
