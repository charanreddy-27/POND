"""Spotify importer: both flavors, timezone handling, idempotency."""

from __future__ import annotations

from pond.importers.spotify import SpotifyImporter


def test_detect(fixtures):
    assert SpotifyImporter.detect(fixtures / "spotify")
    assert SpotifyImporter.detect(fixtures / "spotify" / "StreamingHistory_music_0.json")
    assert not SpotifyImporter.detect(fixtures / "whatsapp")


def test_import_both_flavors(con, cfg, fixtures):
    stats = SpotifyImporter().run(fixtures / "spotify", con, cfg)
    # account: 4 rows; extended: 4 rows minus 1 null-track = 3
    assert stats.rows_inserted == 7
    assert stats.rows_skipped == 0
    assert any("no track name" in w for w in stats.warnings)
    assert con.execute("SELECT count(*) FROM listens").fetchone()[0] == 7


def test_timezone_localized(con, cfg, fixtures):
    SpotifyImporter().run(fixtures / "spotify", con, cfg)
    # endTime 2026-01-05 14:32 UTC == 20:02 IST
    local = con.execute(
        "SELECT strftime(ts, '%H:%M') FROM listens "
        "WHERE track = 'Hukum' AND ts::DATE = DATE '2026-01-05' LIMIT 1"
    ).fetchone()[0]
    assert local == "20:02"


def test_skipped_flag_only_in_extended(con, cfg, fixtures):
    SpotifyImporter().run(fixtures / "spotify", con, cfg)
    assert con.execute("SELECT count(*) FROM listens WHERE skipped IS NOT NULL").fetchone()[0] == 3
    assert (
        con.execute("SELECT skipped FROM listens WHERE track = 'Naatu Naatu'").fetchone()[0] is True
    )


def test_reimport_is_noop(con, cfg, fixtures):
    imp = SpotifyImporter()
    imp.run(fixtures / "spotify", con, cfg)
    again = imp.run(fixtures / "spotify", con, cfg)
    assert again.rows_inserted == 0
    assert again.files_skipped == 2  # ledger short-circuits both files
    forced = imp.run(fixtures / "spotify", con, cfg, force=True)
    assert forced.rows_inserted == 0  # row dedupe still holds
    assert forced.rows_skipped == 7
    assert con.execute("SELECT count(*) FROM listens").fetchone()[0] == 7
