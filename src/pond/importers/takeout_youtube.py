"""Takeout sub-importer: YouTube watch history -> `youtube_watches` (§5.3)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from pond.config import Config
from pond.importers.base import ImportStats, insert_dedupe
from pond.ledger import already_imported, file_sha256, record_import

REL_PATH = Path("YouTube and YouTube Music") / "history" / "watch-history.json"

YT_COLS = ["ts", "title", "channel", "source", "dedupe_key"]


def run(
    root: Path, con: duckdb.DuckDBPyConnection, cfg: Config, force: bool = False
) -> ImportStats | None:
    """Import watch-history.json if present; None means the folder is missing."""
    f = root / REL_PATH
    if not f.exists():
        return None
    stats = ImportStats(tables={"youtube_watches"})
    sha = file_sha256(f)
    if not force and already_imported(con, sha):
        stats.files_skipped += 1
        return stats

    con.execute(
        "CREATE OR REPLACE TEMP TABLE _stg_yt AS "
        "SELECT time::TIMESTAMPTZ AS ts, "
        "       substr(title, len('Watched ') + 1) AS title, "
        "       subtitles[1].name AS channel "  # absent for deleted videos -> NULL
        "FROM read_json(?, columns = {title: 'VARCHAR', time: 'VARCHAR', "
        "     subtitles: 'STRUCT(name VARCHAR, url VARCHAR)[]', "
        "     details: 'STRUCT(name VARCHAR)[]'}) "
        "WHERE title LIKE 'Watched %' "
        "  AND len(list_filter(coalesce(details, []), d -> d.name = 'From Google Ads')) = 0",
        [str(f)],
    )
    select = (
        "SELECT ts, title, channel, 'takeout' AS source, "
        "sha256(concat_ws('|', 'youtube', "
        "  strftime(ts AT TIME ZONE 'UTC', '%Y-%m-%d %H:%M:%S'), coalesce(title,''))) "
        "  AS dedupe_key "
        "FROM _stg_yt"
    )
    ins, skip = insert_dedupe(con, "youtube_watches", YT_COLS, select)
    stats.rows_inserted, stats.rows_skipped = ins, skip
    record_import(con, sha, f, "takeout", ins)
    return stats
