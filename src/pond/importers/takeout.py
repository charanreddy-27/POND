"""Google Takeout importer (§5.3): dispatches into per-product sub-importers."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import duckdb

from pond.config import Config
from pond.importers import (
    takeout_calendar,
    takeout_fit,
    takeout_search,
    takeout_youtube,
)
from pond.importers.base import BaseImporter, ImportStats, register

SUB_IMPORTERS = [
    ("Search history", takeout_search.run),
    ("YouTube history", takeout_youtube.run),
    ("Google Fit", takeout_fit.run),
    ("Calendar", takeout_calendar.run),
]


def find_takeout_root(path: Path) -> Path | None:
    """Locate the `Takeout/` folder at, in, or above ``path``."""
    if path.is_dir():
        if path.name == "Takeout":
            return path
        direct = path / "Takeout"
        if direct.is_dir():
            return direct
        hits = [p for p in path.rglob("Takeout") if p.is_dir()]
        if hits:
            return sorted(hits, key=lambda p: len(p.parts))[0]
    return None


@register
class TakeoutImporter(BaseImporter):
    """A `Takeout/` folder or `takeout-*.zip` (zips are extracted by the CLI)."""

    id = "takeout"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file():
            return fnmatch.fnmatch(path.name.lower(), "takeout-*.zip")
        return find_takeout_root(path) is not None

    def run(
        self,
        path: Path,
        con: duckdb.DuckDBPyConnection,
        cfg: Config,
        force: bool = False,
        **kwargs: object,
    ) -> ImportStats:
        stats = ImportStats()
        root = find_takeout_root(path)
        if root is None:
            stats.warnings.append(f"no Takeout/ folder found under {path}")
            return stats
        for label, runner in SUB_IMPORTERS:
            sub = runner(root, con, cfg, force=force)
            if sub is None:
                stats.warnings.append(f"{label}: not present in this Takeout (skipped)")
                continue
            stats.merge(sub)
        return stats
