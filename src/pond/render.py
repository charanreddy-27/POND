"""Rich table / CSV / JSON output helpers."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal

from rich.console import Console
from rich.table import Table

from pond.importers.base import ImportStats

console = Console()


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def render_table(columns: list[str], rows: list[tuple], title: str | None = None) -> None:
    """Print a result set as a Rich table."""
    if not columns:
        console.print("[dim]OK (no result set)[/dim]")
        return
    table = Table(title=title, header_style="bold cyan")
    for col in columns:
        table.add_column(col, justify="right" if col.lower().endswith(
            ("amount", "spend", "count", "total", "hours", "steps", "calories", "min")
        ) else "left")
    for row in rows:
        table.add_row(*(_fmt(v) for v in row))
    console.print(table)
    console.print(f"[dim]{len(rows)} row{'s' if len(rows) != 1 else ''}[/dim]")


def render_csv(columns: list[str], rows: list[tuple]) -> None:
    """Print a result set as CSV to stdout (for piping)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    print(buf.getvalue(), end="")


def render_json(columns: list[str], rows: list[tuple]) -> None:
    """Print a result set as a JSON array of objects."""
    print(json.dumps(
        [dict(zip(columns, row, strict=False)) for row in rows],
        default=_fmt, indent=2,
    ))


def render_import_summary(source: str, stats: ImportStats, ranges: dict[str, tuple]) -> None:
    """Post-import summary: inserted/skipped/warnings + new table date ranges."""
    parts = [f"[bold green]{stats.rows_inserted}[/bold green] rows inserted"]
    if stats.rows_skipped:
        parts.append(f"[yellow]{stats.rows_skipped}[/yellow] duplicates skipped")
    if stats.files_skipped:
        parts.append(f"[dim]{stats.files_skipped} file(s) already imported[/dim]")
    console.print(f"[bold]{source}[/bold]: " + " · ".join(parts))
    for table, (lo, hi) in sorted(ranges.items()):
        if lo:
            console.print(f"  [cyan]{table}[/cyan] now spans {lo} → {hi}")
    for w in stats.warnings:
        console.print(f"  [yellow]⚠ {w}[/yellow]")
