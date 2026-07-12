"""Takeout sub-importer: Google Search history -> `searches` (§5.3)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from pond.config import Config
from pond.importers.base import ImportStats, insert_dedupe
from pond.ledger import already_imported, file_sha256, record_import

REL_PATH = Path("My Activity") / "Search" / "MyActivity.json"

SEARCH_COLS = ["ts", "query", "source", "dedupe_key"]


def run(
    root: Path, con: duckdb.DuckDBPyConnection, cfg: Config, force: bool = False
) -> ImportStats | None:
    """Import MyActivity.json if present; None means the folder is missing."""
    f = root / REL_PATH
    if not f.exists():
        return None
    stats = ImportStats(tables={"searches"})
    sha = file_sha256(f)
    if not force and already_imported(con, sha):
        stats.files_skipped += 1
        return stats

    con.execute(
        "CREATE OR REPLACE TEMP TABLE _stg_search AS "
        "SELECT time::TIMESTAMPTZ AS ts, "
        "       substr(title, len('Searched for ') + 1) AS query "
        "FROM read_json(?, columns = {title: 'VARCHAR', time: 'VARCHAR'}) "
        "WHERE title LIKE 'Searched for %'",  # 'Visited …' entries are ignored
        [str(f)],
    )
    select = (
        "SELECT ts, query, 'google_search' AS source, "
        "sha256(concat_ws('|', 'google_search', "
        "  strftime(ts AT TIME ZONE 'UTC', '%Y-%m-%d %H:%M:%S'), query)) AS dedupe_key "
        "FROM _stg_search WHERE query IS NOT NULL AND query <> ''"
    )
    ins, skip = insert_dedupe(con, "searches", SEARCH_COLS, select)
    stats.rows_inserted, stats.rows_skipped = ins, skip
    record_import(con, sha, f, "takeout", ins)
    return stats
