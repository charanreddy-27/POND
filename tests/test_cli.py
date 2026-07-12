"""End-to-end CLI flows via Typer's test runner."""

from __future__ import annotations

from typer.testing import CliRunner

from pond import db
from pond.cli import app
from pond.config import load_config

runner = CliRunner()


def test_init_and_status(pond_env):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (pond_env / "config.yaml").exists()
    assert (pond_env / "category_rules.yaml").exists()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "transactions" in result.output


def test_import_spotify_via_cli(pond_env, fixtures):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["import", str(fixtures / "spotify")])
    assert result.exit_code == 0, result.output
    assert "7" in result.output  # rows inserted
    # second run: ledger makes it a no-op
    result = runner.invoke(app, ["import", str(fixtures / "spotify")])
    assert result.exit_code == 0
    assert "already imported" in result.output


def test_log_gym_flips_weeks_view(pond_env):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["log", "gym", "--date", "2026-07-06", "--minutes", "45"])
    assert result.exit_code == 0, result.output
    assert "strength_training" in result.output

    # same day again -> duplicate, refused
    result = runner.invoke(app, ["log", "gym", "--date", "2026-07-06"])
    assert "already logged" in result.output

    cfg = load_config()
    con = db.connect(cfg)
    flagged = con.execute(
        "SELECT went_to_gym FROM weeks WHERE week_start = DATE '2026-07-06'"
    ).fetchone()[0]
    con.close()
    assert flagged is True


def test_sql_command(pond_env):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["sql", "SELECT 1 AS one", "--format", "csv"])
    assert result.exit_code == 0
    assert "one" in result.output and "1" in result.output


def test_unknown_source_rejected(pond_env, fixtures):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["import", str(fixtures / "spotify"), "--source", "nope"])
    assert result.exit_code != 0
