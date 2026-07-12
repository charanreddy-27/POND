"""Takeout dispatcher + the four sub-importers against a synthetic Takeout tree."""

from __future__ import annotations

from pond.importers.takeout import TakeoutImporter


def test_detect(fixtures):
    assert TakeoutImporter.detect(fixtures / "takeout")
    assert TakeoutImporter.detect(fixtures / "takeout" / "Takeout")
    assert not TakeoutImporter.detect(fixtures / "spotify")


def test_full_takeout_import(con, cfg, fixtures):
    stats = TakeoutImporter().run(fixtures / "takeout", con, cfg)

    # Search: 3 'Searched for' entries, one an exact duplicate -> 2 rows.
    queries = con.execute("SELECT query FROM searches ORDER BY ts").fetchall()
    assert [q[0] for q in queries] == [
        "duckdb window functions",
        "best badminton racket under 5000",
    ]

    # YouTube: 4 entries minus 1 ad = 3; deleted video has NULL channel.
    yt = con.execute(
        "SELECT title, channel FROM youtube_watches ORDER BY ts"
    ).fetchall()
    assert len(yt) == 3
    assert yt[0] == ("DuckDB in 100 Seconds", "Fireship")
    assert yt[1][1] is None
    assert all("sponsored" not in t.lower() for t, _ in yt)

    # Fit dailies: 3 days mapped through fuzzy headers.
    day = con.execute(
        "SELECT steps, distance_m, calories, active_min FROM daily_metrics "
        "WHERE day = DATE '2026-02-11'"
    ).fetchone()
    assert day == (12050, 9530.8, 2540.9, 105.0)
    assert any("Heart Points" in w for w in stats.warnings)  # unknown column reported

    # Fit sessions: weightlifting + badminton, normalized snake_case.
    acts = con.execute(
        "SELECT activity_type, duration_min, calories FROM activities ORDER BY ts_start"
    ).fetchall()
    assert acts == [("weightlifting", 65.0, 412.7), ("badminton", 60.0, 350.2)]

    # Calendar: 3 events, one all-day, recurring stored as master only.
    events = con.execute(
        "SELECT title, all_day FROM calendar_events ORDER BY ts_start"
    ).fetchall()
    assert ("Amma birthday", True) in events
    assert len(events) == 3

    assert stats.rows_inserted == 2 + 3 + 3 + 2 + 3


def test_weeks_view_sees_fit_gym(con, cfg, fixtures):
    TakeoutImporter().run(fixtures / "takeout", con, cfg)
    flagged = con.execute(
        "SELECT went_to_gym FROM weeks WHERE week_start = DATE '2026-02-09'"
    ).fetchone()[0]
    assert flagged is True


def test_reimport_is_noop(con, cfg, fixtures):
    imp = TakeoutImporter()
    imp.run(fixtures / "takeout", con, cfg)
    again = imp.run(fixtures / "takeout", con, cfg)
    assert again.rows_inserted == 0
    assert again.files_skipped == 6  # search, yt, dailies, 2 sessions, ics


def test_missing_products_reported_not_fatal(con, cfg, tmp_path):
    (tmp_path / "Takeout" / "Calendar").mkdir(parents=True)
    stats = TakeoutImporter().run(tmp_path, con, cfg)
    assert stats.rows_inserted == 0
    assert sum("not present" in w for w in stats.warnings) == 3
