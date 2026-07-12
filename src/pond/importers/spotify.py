"""Spotify importer: account-data and extended streaming history JSON (§5.1)."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import duckdb

from pond.config import Config
from pond.importers.base import BaseImporter, ImportStats, register
from pond.ledger import already_imported, file_sha256, record_import

ACCOUNT_GLOB = "StreamingHistory*.json"
EXTENDED_GLOB = "Streaming_History_Audio_*.json"

# Deterministic dedupe key: normalize ts to a UTC wall-clock string so the key
# never depends on the session timezone (§3.4: listens = ts|track|artist|ms_played).
_KEY = (
    "sha256(concat_ws('|', 'spotify', "
    "strftime(ts AT TIME ZONE 'UTC', '%Y-%m-%d %H:%M:%S'), "
    "coalesce(track,''), coalesce(artist,''), coalesce(ms_played::VARCHAR,'')))"
)

LISTEN_COLS = ["ts", "track", "artist", "album", "ms_played", "skipped", "source", "dedupe_key"]


def _spotify_files(path: Path) -> list[Path]:
    """All Spotify history files under ``path`` (or [path] if it is one)."""
    if path.is_file():
        name = path.name
        if fnmatch.fnmatch(name, ACCOUNT_GLOB) or fnmatch.fnmatch(name, EXTENDED_GLOB):
            return [path]
        return []
    files = list(path.rglob(ACCOUNT_GLOB)) + list(path.rglob(EXTENDED_GLOB))
    return sorted(set(files))


@register
class SpotifyImporter(BaseImporter):
    """Handles both export flavors; both land in the ``listens`` table."""

    id = "spotify"

    @classmethod
    def detect(cls, path: Path) -> bool:
        return bool(_spotify_files(path))

    def run(
        self,
        path: Path,
        con: duckdb.DuckDBPyConnection,
        cfg: Config,
        force: bool = False,
        **kwargs: object,
    ) -> ImportStats:
        stats = ImportStats(tables={"listens"})
        for f in _spotify_files(path):
            sha = file_sha256(f)
            if not force and already_imported(con, sha):
                stats.files_skipped += 1
                continue
            if fnmatch.fnmatch(f.name, EXTENDED_GLOB):
                ins, skip, warns = self._load_extended(con, f)
            else:
                ins, skip, warns = self._load_account(con, f)
            stats.rows_inserted += ins
            stats.rows_skipped += skip
            stats.warnings.extend(warns)
            record_import(con, sha, f, self.id, ins)
        return stats

    def _load_account(
        self, con: duckdb.DuckDBPyConnection, f: Path
    ) -> tuple[int, int, list[str]]:
        """StreamingHistory*.json — endTime is UTC at minute precision."""
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _stg_listens AS "
            "SELECT strptime(endTime, '%Y-%m-%d %H:%M') AT TIME ZONE 'UTC' AS ts, "
            "       trackName AS track, artistName AS artist, "
            "       NULL::VARCHAR AS album, msPlayed AS ms_played, "
            "       NULL::BOOLEAN AS skipped, 'spotify' AS source "
            "FROM read_json(?, columns = {endTime: 'VARCHAR', artistName: 'VARCHAR', "
            "                             trackName: 'VARCHAR', msPlayed: 'BIGINT'})",
            [str(f)],
        )
        self._stage_raw(con, "raw_spotify_account", f)
        return (*self._insert(con), [])

    def _load_extended(
        self, con: duckdb.DuckDBPyConnection, f: Path
    ) -> tuple[int, int, list[str]]:
        """Streaming_History_Audio_*.json — lifetime history, second precision."""
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _stg_listens AS "
            "SELECT strptime(ts, '%Y-%m-%dT%H:%M:%SZ') AT TIME ZONE 'UTC' AS ts, "
            "       master_metadata_track_name AS track, "
            "       master_metadata_album_artist_name AS artist, "
            "       master_metadata_album_album_name AS album, "
            "       ms_played, skipped, 'spotify' AS source "
            "FROM read_json(?, columns = {ts: 'VARCHAR', ms_played: 'BIGINT', "
            "     \"master_metadata_track_name\": 'VARCHAR', "
            "     \"master_metadata_album_artist_name\": 'VARCHAR', "
            "     \"master_metadata_album_album_name\": 'VARCHAR', skipped: 'BOOLEAN'})",
            [str(f)],
        )
        self._stage_raw(con, "raw_spotify_extended", f)
        warns: list[str] = []
        n_null = con.execute(
            "SELECT count(*) FROM _stg_listens WHERE track IS NULL"
        ).fetchone()[0]
        if n_null:
            warns.append(f"{f.name}: skipped {n_null} rows with no track name (podcasts?)")
            con.execute("DELETE FROM _stg_listens WHERE track IS NULL")
        ins, skip = self._insert(con)
        return ins, skip, warns

    @staticmethod
    def _stage_raw(con: duckdb.DuckDBPyConnection, table: str, f: Path) -> None:
        """Keep the file as-is in a raw_* table for debugging (§3.1)."""
        try:
            exists = con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
            ).fetchone()[0]
            if not exists:
                con.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_json_auto(?)", [str(f)]
                )
            else:
                con.execute(
                    f"INSERT INTO {table} BY NAME SELECT * FROM read_json_auto(?)", [str(f)]
                )
        except duckdb.Error:
            # Raw staging is best-effort; curated load reads the file directly.
            pass

    @staticmethod
    def _insert(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
        from pond.importers.base import insert_dedupe

        select = f"SELECT *, {_KEY} AS dedupe_key FROM _stg_listens"
        return insert_dedupe(con, "listens", LISTEN_COLS, select)
