"""Phase-6 polish: raw_* staging tables, `pond schema`, numeric summary footer."""

from __future__ import annotations

from typer.testing import CliRunner

from pond.cli import app
from pond.importers.spotify import SpotifyImporter
from pond.importers.takeout import TakeoutImporter
from pond.render import _numeric_summary

runner = CliRunner()


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def test_spotify_stages_raw(con, cfg, fixtures):
    SpotifyImporter().run(fixtures / "spotify", con, cfg)
    tables = _tables(con)
    assert {"raw_spotify_account", "raw_spotify_extended"} <= tables
    # raw keeps everything as-is, including the null-track podcast row
    assert con.execute("SELECT count(*) FROM raw_spotify_extended").fetchone()[0] == 4


def test_takeout_stages_raw(con, cfg, fixtures):
    TakeoutImporter().run(fixtures / "takeout", con, cfg)
    assert {
        "raw_takeout_search",
        "raw_takeout_youtube",
        "raw_fit_daily",
        "raw_fit_sessions",
        "raw_calendar",
    } <= _tables(con)
    # raw search keeps the 'Visited …' entry that the curated table drops
    assert con.execute("SELECT count(*) FROM raw_takeout_search").fetchone()[0] == 4


def test_schema_command(pond_env):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0, result.output
    assert "CREATE TABLE transactions" in result.output
    assert "vocab_sharing" in result.output


def test_numeric_summary_footer():
    cols = ["week_start", "went_to_gym", "spend"]
    rows = [("2026-01-05", False, 100), ("2026-01-12", True, 200)]
    footer = _numeric_summary(cols, rows)
    assert "spend" in footer and "total 300" in footer and "avg 150" in footer
    # booleans are never summarized; single rows get no footer
    assert _numeric_summary(cols, rows[:1]) == ""
    assert "went_to_gym" not in footer
