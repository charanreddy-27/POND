"""WhatsApp parser: both styles, continuations, media, system messages, is_me."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from pond.importers.whatsapp import (
    WhatsAppImporter,
    chat_name_from_file,
    detect_dayfirst,
    parse_chat,
)

TZ = ZoneInfo("Asia/Kolkata")


def test_chat_names(tmp_path):
    assert chat_name_from_file(tmp_path / "WhatsApp Chat with Rahul.txt") == "Rahul"
    assert chat_name_from_file(tmp_path / "Gym Buddies_chat.txt") == "Gym Buddies"


def test_dayfirst_detection():
    dayfirst, ambiguous = detect_dayfirst(["13/03/24, 9:14 pm - A: hi"])
    assert dayfirst and not ambiguous
    dayfirst, ambiguous = detect_dayfirst(["03/13/24, 9:14 pm - A: hi"])
    assert not dayfirst and not ambiguous
    dayfirst, ambiguous = detect_dayfirst(["03/04/24, 9:14 pm - A: hi"])
    assert dayfirst and ambiguous  # default DD/MM with a warning


def test_android_parse(fixtures):
    text = (fixtures / "whatsapp" / "WhatsApp Chat with Rahul.txt").read_text()
    msgs, warnings = parse_chat(text, TZ)
    assert len(msgs) == 5  # system line dropped
    assert msgs[1].text == "yeah 7pm\ncourt 3 as usual"  # continuation joined
    media = [m for m in msgs if m.is_media]
    assert len(media) == 1 and media[0].text is None
    assert msgs[0].ts.day == 12 and msgs[0].ts.month == 3  # DD/MM won
    assert msgs[-1].ts.hour == 22  # 24h clock line parsed


def test_ios_parse(fixtures):
    text = (fixtures / "whatsapp" / "Gym Buddies_chat.txt").read_text()
    msgs, _ = parse_chat(text, TZ)
    assert len(msgs) == 4
    assert msgs[2].is_media  # "image omitted" behind an invisible mark
    assert msgs[3].ts.day == 13 and msgs[3].ts.hour == 21


def test_import_sets_is_me_and_dedupes(con, cfg, fixtures):
    imp = WhatsAppImporter()
    stats = imp.run(fixtures / "whatsapp", con, cfg)
    assert stats.rows_inserted == 9
    mine = con.execute("SELECT count(*) FROM messages WHERE is_me").fetchone()[0]
    assert mine == 3  # Charan appears 2x in Rahul chat, 1x in Gym Buddies
    wc = con.execute(
        "SELECT word_count FROM messages WHERE text = 'nice pic'"
    ).fetchone()[0]
    assert wc == 2
    again = imp.run(fixtures / "whatsapp", con, cfg)
    assert again.rows_inserted == 0 and again.files_skipped == 2
