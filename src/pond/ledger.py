"""The `_imports` file ledger: sha256 hashing + idempotency helpers (§3.4)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb


def file_sha256(path: Path) -> str:
    """Streaming sha256 of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def already_imported(con: duckdb.DuckDBPyConnection, sha: str) -> bool:
    """True if this exact file (by content hash) was imported before."""
    row = con.execute("SELECT 1 FROM _imports WHERE file_sha256 = ?", [sha]).fetchone()
    return row is not None


def record_import(
    con: duckdb.DuckDBPyConnection,
    sha: str,
    path: Path,
    source: str,
    rows_inserted: int,
) -> None:
    """Upsert a ledger entry for a processed file."""
    con.execute(
        """INSERT INTO _imports (file_sha256, path, source, rows_inserted)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (file_sha256) DO UPDATE SET
             path = excluded.path, imported_at = now(),
             rows_inserted = excluded.rows_inserted""",
        [sha, str(path), source, rows_inserted],
    )


def row_key(*parts: object) -> str:
    """sha256 dedupe key over the canonical natural-key fields (Python side).

    SQL-side importers build the equivalent with DuckDB's sha256(concat_ws(...)).
    """
    canon = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
