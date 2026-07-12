"""Takeout sub-importer: Calendar .ics -> `calendar_events` (§5.3).

Limitation (MVP): recurring events are stored as their master VEVENT only —
no recurrence expansion.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from icalendar import Calendar

from pond.config import Config
from pond.importers.base import ImportStats, insert_dedupe
from pond.ledger import already_imported, file_sha256, record_import, row_key

CAL_DIR = "Calendar"

EVENT_COLS = ["ts_start", "ts_end", "title", "all_day", "source", "dedupe_key"]


def _to_ts(value: object, tz: ZoneInfo) -> tuple[datetime | None, bool]:
    """Normalize an .ics DTSTART/DTEND value -> (aware datetime, is_all_day)."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=tz)), False
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz), True
    return None, False


def run(
    root: Path, con: duckdb.DuckDBPyConnection, cfg: Config, force: bool = False
) -> ImportStats | None:
    """Import every .ics under Takeout/Calendar; None if the folder is missing."""
    cal_dir = root / CAL_DIR
    if not cal_dir.is_dir():
        return None
    stats = ImportStats(tables={"calendar_events"})
    tz = ZoneInfo(cfg.me.timezone)

    for f in sorted(cal_dir.glob("*.ics")):
        sha = file_sha256(f)
        if not force and already_imported(con, sha):
            stats.files_skipped += 1
            continue
        try:
            cal = Calendar.from_ical(f.read_bytes())
        except ValueError as e:
            stats.warnings.append(f"{f.name}: unparseable ics ({e})")
            continue

        rows = []
        for ev in cal.walk("VEVENT"):
            dtstart = ev.get("DTSTART")
            if dtstart is None:
                continue
            ts_start, all_day = _to_ts(dtstart.dt, tz)
            if ts_start is None:
                continue
            dtend = ev.get("DTEND")
            ts_end = _to_ts(dtend.dt, tz)[0] if dtend is not None else None
            title = str(ev.get("SUMMARY", "")) or None
            uid = str(ev.get("UID", "")) or None
            # §3.4: UID is the natural key; fall back to ts_start|title.
            key = row_key("calendar", uid) if uid else row_key(
                "calendar", ts_start.isoformat(), title
            )
            rows.append((ts_start, ts_end, title, all_day, "google_calendar", key))

        con.execute(
            "CREATE OR REPLACE TEMP TABLE _stg_cal "
            "(ts_start TIMESTAMPTZ, ts_end TIMESTAMPTZ, title VARCHAR, all_day BOOLEAN, "
            " source VARCHAR, dedupe_key VARCHAR)"
        )
        if rows:
            con.executemany("INSERT INTO _stg_cal VALUES (?,?,?,?,?,?)", rows)
        ins, skip = insert_dedupe(con, "calendar_events", EVENT_COLS, "SELECT * FROM _stg_cal")
        stats.rows_inserted += ins
        stats.rows_skipped += skip
        record_import(con, sha, f, "takeout", ins)
    return stats
