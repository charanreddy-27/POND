"""POND CLI — thin Typer app delegating to the real modules."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from pond import __version__, db
from pond.config import init_home, load_config, pond_home

app = typer.Typer(
    name="pond",
    help="POND — query your life. Local-first personal data warehouse.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()

ACTIVITY_ALIASES = {"gym": "strength_training", "weights": "strength_training"}


@app.command()
def init(
    name: str = typer.Option(None, "--name", help="Your name as it appears in WhatsApp exports"),
    timezone: str = typer.Option(None, "--timezone", help="IANA timezone, e.g. Asia/Kolkata"),
) -> None:
    """Create ~/.pond with config, starter category rules, and an empty database."""
    cfg = init_home(whatsapp_name=name, timezone=timezone)
    con = db.connect(cfg)
    con.close()
    console.print(f"[green]✓[/green] POND home ready at [bold]{pond_home()}[/bold]")
    console.print(f"  database: {cfg.db_path}")
    console.print(f"  timezone: {cfg.me.timezone} · whatsapp name: {cfg.me.whatsapp_name}")
    console.print("Next: [bold]pond import <your export file/folder/zip>[/bold]")


@app.command(name="import")
def import_(
    path: Path = typer.Argument(..., exists=True, help="Export file, folder, or .zip"),
    source: str = typer.Option(None, "--source", help="Force an importer: "
                               "spotify | whatsapp | takeout | bank"),
    account: str = typer.Option(None, "--account", help="Account label for bank imports"),
    force: bool = typer.Option(False, "--force", help="Re-import files already in the ledger"),
) -> None:
    """Import an export into the warehouse. Safe to re-run — duplicates are no-ops."""
    from pond.importers import REGISTRY, detect_importers
    from pond.importers.base import maybe_extract_zip
    from pond.render import render_import_summary

    cfg = load_config()
    con = db.connect(cfg)

    work_path, tmp = maybe_extract_zip(path)
    try:
        if source:
            if source not in REGISTRY:
                raise typer.BadParameter(
                    f"unknown source {source!r}; choose from {sorted(REGISTRY)}"
                )
            importers = [REGISTRY[source]]
        else:
            importers = detect_importers(work_path)
            if not importers:
                console.print(f"[red]No importer recognizes {path}.[/red] "
                              f"Try --source {sorted(REGISTRY)}")
                raise typer.Exit(1)
            if len(importers) > 1:
                console.print(
                    "[yellow]Ambiguous input[/yellow] — candidates: "
                    + ", ".join(i.id for i in importers)
                    + ". Re-run with --source <id>."
                )
                raise typer.Exit(1)

        cls = importers[0]
        with console.status(f"importing via [bold]{cls.id}[/bold]…"):
            stats = cls().run(work_path, con, cfg, force=force, account=account)
        ts_col = {
            "transactions": "ts", "messages": "ts", "listens": "ts",
            "activities": "ts_start", "daily_metrics": "day", "searches": "ts",
            "youtube_watches": "ts", "calendar_events": "ts_start",
        }
        ranges = {t: db.date_range(con, t, ts_col[t]) for t in stats.tables}
        render_import_summary(cls.id, stats, ranges)
    finally:
        if tmp:
            tmp.cleanup()
        con.close()


@app.command()
def log(
    activity: str = typer.Argument(..., help="e.g. gym, badminton, walking"),
    date: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    minutes: float = typer.Option(None, "--minutes", help="Duration in minutes"),
) -> None:
    """Manually log an activity — the escape hatch for signals with no data trail."""
    from pond.ledger import row_key

    cfg = load_config()
    con = db.connect(cfg)
    tz = ZoneInfo(cfg.me.timezone)
    activity_type = ACTIVITY_ALIASES.get(activity.lower(), activity.lower().replace(" ", "_"))
    # DECISION: --date entries land at 12:00 local so they bucket into the
    # right local day/week regardless of timezone math.
    if date:
        ts_start = datetime.combine(
            datetime.strptime(date, "%Y-%m-%d").date(), time(12, 0), tzinfo=tz
        )
    else:
        ts_start = datetime.now(tz)
    key = row_key("manual", ts_start.strftime("%Y-%m-%d"), activity_type)
    inserted = con.execute(
        """INSERT INTO activities (ts_start, activity_type, duration_min, source, dedupe_key)
           SELECT ?, ?, ?, 'manual', ?
           WHERE ? NOT IN (SELECT dedupe_key FROM activities)""",
        [ts_start, activity_type, minutes, key, key],
    ).fetchone()[0]
    con.close()
    if inserted:
        extra = f" ({minutes:g} min)" if minutes else ""
        console.print(f"[green]✓[/green] logged [bold]{activity_type}[/bold] "
                      f"on {ts_start:%Y-%m-%d}{extra}")
    else:
        console.print(f"[yellow]already logged[/yellow] {activity_type} on {ts_start:%Y-%m-%d}")


@app.command()
def categorize() -> None:
    """Re-run merchant extraction + category rules over all transactions."""
    from pond.categorize import recategorize_all

    cfg = load_config()
    con = db.connect(cfg)
    n = recategorize_all(con)
    total = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    con.close()
    console.print(f"[green]✓[/green] {n}/{total} transactions categorized")


@app.command()
def sql(
    query: str = typer.Argument(..., help="Raw SQL to run against the warehouse"),
    fmt: str = typer.Option("table", "--format", "-f", help="table | csv | json"),
) -> None:
    """Run raw SQL locally (no LLM involved)."""
    from pond.ask.engine import run_sql
    from pond.render import render_csv, render_json, render_table

    cfg = load_config()
    con = db.connect(cfg)
    try:
        result = run_sql(query, con)
    except Exception as e:  # noqa: BLE001 — surface DuckDB errors cleanly
        console.print(f"[red]SQL error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        con.close()
    {"table": render_table, "csv": render_csv, "json": render_json}.get(
        fmt, render_table
    )(result.columns, result.rows)


@app.command()
def ask(
    question: str = typer.Argument(..., help="A plain-English question about your data"),
    explain: bool = typer.Option(False, "--explain", help="Show the generated SQL"),
    fmt: str = typer.Option("table", "--format", "-f", help="table | csv | json"),
) -> None:
    """Ask in plain English. Only your schema is sent to the LLM — never your data."""
    from pond.ask.engine import AskError
    from pond.ask.engine import ask as run_ask
    from pond.render import render_csv, render_json, render_table

    cfg = load_config()
    con = db.connect(cfg)
    try:
        with console.status("thinking…"):
            result = run_ask(question, con, cfg)
    except AskError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    finally:
        con.close()
    if explain:
        console.print(f"[dim]{result.sql}[/dim]\n")
    {"table": render_table, "csv": render_csv, "json": render_json}.get(
        fmt, render_table
    )(result.columns, result.rows)
    if result.attempts > 1:
        console.print(f"[dim](took {result.attempts} attempts)[/dim]")


@app.command()
def status() -> None:
    """What's in the pond: row counts and date ranges per table."""
    cfg = load_config()
    if not db.db_exists(cfg):
        console.print("No database yet — run [bold]pond init[/bold] first.")
        raise typer.Exit(1)
    con = db.connect(cfg)
    table = Table(title=f"🦆 {cfg.db_path}", header_style="bold cyan")
    for col in ("table", "rows", "from", "to"):
        table.add_column(col, justify="right" if col == "rows" else "left")
    for name, n, lo, hi in db.table_summary(con):
        table.add_row(name, f"{n:,}", lo or "—", hi or "—")
    imports = con.execute("SELECT count(*) FROM _imports").fetchone()[0]
    con.close()
    console.print(table)
    console.print(f"[dim]{imports} file(s) in the import ledger[/dim]")


@app.command()
def version() -> None:
    """Print the POND version."""
    console.print(f"pond {__version__}")


if __name__ == "__main__":
    app()
