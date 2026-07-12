"""Schema migrations, spine views, and ledger basics."""

from __future__ import annotations

from pond import db
from pond.ledger import already_imported, file_sha256, record_import


def test_curated_tables_exist(con):
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert set(db.CURATED_TABLES) <= tables
    assert "_imports" in tables


def test_spine_views_exist_and_cover_a_year(con):
    n_days = con.execute("SELECT count(*) FROM days").fetchone()[0]
    assert n_days >= 365
    weeks = con.execute("SELECT count(*), bool_or(went_to_gym) FROM weeks").fetchone()
    assert weeks[0] >= 52
    assert weeks[1] is False  # empty database -> no gym anywhere


def test_weeks_flags_gym_after_activity(con):
    con.execute(
        "INSERT INTO activities (ts_start, activity_type, source, dedupe_key) "
        "VALUES (CURRENT_DATE - INTERVAL 3 DAY, 'strength_training', 'manual', 'k1')"
    )
    gym_weeks = con.execute(
        "SELECT count(*) FROM weeks WHERE went_to_gym"
    ).fetchone()[0]
    assert gym_weeks == 1


def test_ledger_roundtrip(con, fixtures):
    f = fixtures / "spotify" / "StreamingHistory_music_0.json"
    sha = file_sha256(f)
    assert not already_imported(con, sha)
    record_import(con, sha, f, "spotify", 4)
    assert already_imported(con, sha)
    record_import(con, sha, f, "spotify", 0)  # upsert must not raise


def test_migrations_are_idempotent(cfg):
    c1 = db.connect(cfg)
    c1.close()
    c2 = db.connect(cfg)
    c2.execute("SELECT count(*) FROM days").fetchone()
    c2.close()
