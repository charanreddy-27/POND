"""BaseImporter ABC, importer registry, auto-detection, and shared load helpers."""

from __future__ import annotations

import tempfile
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from pond.config import Config


@dataclass
class ImportStats:
    """Outcome of one importer run, for the Rich summary after `pond import`."""

    rows_inserted: int = 0
    rows_skipped: int = 0
    files_skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)

    def merge(self, other: ImportStats) -> None:
        """Fold another stats object into this one."""
        self.rows_inserted += other.rows_inserted
        self.rows_skipped += other.rows_skipped
        self.files_skipped += other.files_skipped
        self.warnings.extend(other.warnings)
        self.tables |= other.tables


class BaseImporter(ABC):
    """One importer per source. Subclasses register themselves via @register."""

    id: str = ""

    @classmethod
    @abstractmethod
    def detect(cls, path: Path) -> bool:
        """Can this importer handle this path?"""

    @abstractmethod
    def run(
        self,
        path: Path,
        con: duckdb.DuckDBPyConnection,
        cfg: Config,
        force: bool = False,
        **kwargs: object,
    ) -> ImportStats:
        """Import ``path`` into the warehouse. Must be idempotent (§3.4)."""


REGISTRY: dict[str, type[BaseImporter]] = {}


def register(cls: type[BaseImporter]) -> type[BaseImporter]:
    """Class decorator adding an importer to the registry."""
    REGISTRY[cls.id] = cls
    return cls


def detect_importers(path: Path) -> list[type[BaseImporter]]:
    """All importers whose detect() accepts this path.

    Bank is the lowest-priority catch-all: it is dropped whenever any other
    importer also matches.
    """
    matches = [cls for cls in REGISTRY.values() if cls.detect(path)]
    if len(matches) > 1:
        matches = [m for m in matches if m.id != "bank"] or matches
    return matches


def maybe_extract_zip(path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """If ``path`` is a .zip, extract to a temp dir and return its root.

    Caller must keep the returned TemporaryDirectory alive while working.
    """
    if path.suffix.lower() != ".zip":
        return path, None
    tmp = tempfile.TemporaryDirectory(prefix="pond_zip_")
    with zipfile.ZipFile(path) as zf:
        zf.extractall(tmp.name)
    return Path(tmp.name), tmp


def insert_dedupe(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[str],
    select_sql: str,
    params: list[object] | None = None,
) -> tuple[int, int]:
    """INSERT ... SELECT with row-level dedupe against ``table.dedupe_key``.

    ``select_sql`` must produce exactly ``columns`` (including dedupe_key).
    Duplicates *within* the batch are collapsed too. Returns
    (rows_inserted, rows_skipped_as_duplicates).
    """
    total = con.execute(f"SELECT count(*) FROM ({select_sql})", params or []).fetchone()[0]
    cols = ", ".join(columns)
    inserted = con.execute(
        f"""
        INSERT INTO {table} ({cols})
        SELECT {cols} FROM (
            SELECT *, row_number() OVER (PARTITION BY dedupe_key) AS _rn
            FROM ({select_sql})
        ) s
        WHERE s._rn = 1
          AND s.dedupe_key NOT IN (SELECT dedupe_key FROM {table})
        """,
        params or [],
    ).fetchone()[0]
    return inserted, total - inserted
