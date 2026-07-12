"""WhatsApp chat export importer (§5.2): Android and iOS `.txt` formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from dateutil import parser as dateparser

from pond.config import Config
from pond.importers.base import BaseImporter, ImportStats, insert_dedupe, register, stage_raw
from pond.ledger import already_imported, file_sha256, record_import, row_key

# Android: "12/03/24, 9:14 pm - Charan: bro match today?"
ANDROID_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[apAP][mM])?)\s-\s(?P<rest>.*)$"
)
# iOS: "[12/03/24, 9:14:33 PM] Charan: bro match today?"
IOS_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[apAP][mM])?)\]\s(?P<rest>.*)$"
)

INVISIBLES = re.compile("[\u200e\u200f\ufeff]")

MEDIA_MARKERS = (
    "<media omitted>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "gif omitted",
    "document omitted",
)

MESSAGE_COLS = [
    "ts",
    "chat",
    "sender",
    "is_me",
    "text",
    "is_media",
    "word_count",
    "source",
    "dedupe_key",
]


@dataclass
class _Msg:
    ts: datetime
    sender: str
    text: str | None
    is_media: bool


def chat_name_from_file(path: Path) -> str:
    """'WhatsApp Chat with Rahul.txt' -> 'Rahul'; 'Family_chat.txt' -> 'Family'."""
    name = path.stem
    if name.lower().startswith("whatsapp chat with "):
        name = name[len("WhatsApp Chat with ") :]
    if name.endswith("_chat"):
        name = name[: -len("_chat")]
    return name.strip() or path.stem


def _clean(line: str) -> str:
    """Strip invisible unicode marks and normalize NBSP to space."""
    return INVISIBLES.sub("", line).replace("\u00a0", " ")


def detect_dayfirst(lines: list[str]) -> tuple[bool, bool]:
    """Scan every timestamped line to resolve DD/MM vs MM/DD (§5.2).

    Returns (dayfirst, was_ambiguous). Any first component > 12 -> DD/MM;
    any second component > 12 -> MM/DD; otherwise default DD/MM (India).
    """
    for line in lines:
        m = ANDROID_RE.match(line) or IOS_RE.match(line)
        if not m:
            continue
        first, second = (int(x) for x in m.group("date").split("/")[:2])
        if first > 12:
            return True, False
        if second > 12:
            return False, False
    return True, True


def parse_chat(text: str, tz: ZoneInfo) -> tuple[list[_Msg], list[str]]:
    """Parse a full export into messages, returning (messages, warnings)."""
    lines = [_clean(ln) for ln in text.splitlines()]
    dayfirst, ambiguous = detect_dayfirst(lines)
    warnings: list[str] = []
    if ambiguous:
        warnings.append("date order ambiguous; defaulting to DD/MM (India)")

    # Lock onto whichever style matches the first message line.
    style: re.Pattern[str] | None = None
    for ln in lines:
        if ANDROID_RE.match(ln):
            style = ANDROID_RE
            break
        if IOS_RE.match(ln):
            style = IOS_RE
            break
    if style is None:
        return [], ["no WhatsApp message lines recognized"]

    msgs: list[_Msg] = []
    for ln in lines:
        m = style.match(ln)
        if not m:
            # Continuation line: append to the previous message's text.
            if msgs and ln.strip():
                prev = msgs[-1]
                prev.text = (prev.text or "") + "\n" + ln
            continue
        rest = m.group("rest")
        if ": " not in rest:
            continue  # system message ("Messages are end-to-end encrypted", …)
        sender, body = rest.split(": ", 1)
        try:
            ts = dateparser.parse(
                f"{m.group('date')} {m.group('time')}", dayfirst=dayfirst
            ).replace(tzinfo=tz)
        except (ValueError, OverflowError):
            warnings.append(f"unparseable timestamp: {ln[:60]!r}")
            continue
        body_l = body.strip().lower()
        is_media = body_l in MEDIA_MARKERS or body_l.startswith("<attached:")
        msgs.append(
            _Msg(ts=ts, sender=sender.strip(), text=None if is_media else body, is_media=is_media)
        )
    return msgs, warnings


@register
class WhatsAppImporter(BaseImporter):
    """Per-chat `.txt` exports ("Export chat -> Without media")."""

    id = "whatsapp"

    @classmethod
    def detect(cls, path: Path) -> bool:
        return bool(cls._chat_files(path))

    @staticmethod
    def _chat_files(path: Path) -> list[Path]:
        candidates = [path] if path.is_file() else sorted(path.rglob("*.txt"))
        out = []
        for f in candidates:
            if f.suffix.lower() != ".txt":
                continue
            if f.name.startswith("WhatsApp Chat with ") or f.stem.endswith("_chat"):
                out.append(f)
                continue
            try:
                head = f.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            first = next((ln for ln in head.splitlines() if ln.strip()), "")
            first = _clean(first)
            if ANDROID_RE.match(first) or IOS_RE.match(first):
                out.append(f)
        return out

    def run(
        self,
        path: Path,
        con: duckdb.DuckDBPyConnection,
        cfg: Config,
        force: bool = False,
        **kwargs: object,
    ) -> ImportStats:
        stats = ImportStats(tables={"messages"})
        tz = ZoneInfo(cfg.me.timezone)
        me = cfg.me.whatsapp_name.strip().lower()
        for f in self._chat_files(path):
            sha = file_sha256(f)
            if not force and already_imported(con, sha):
                stats.files_skipped += 1
                continue
            chat = chat_name_from_file(f)
            msgs, warns = parse_chat(f.read_text(encoding="utf-8", errors="replace"), tz)
            stats.warnings.extend(f"{f.name}: {w}" for w in warns)

            rows = [
                (
                    m.ts,
                    chat,
                    m.sender,
                    m.sender.strip().lower() == me,
                    m.text,
                    m.is_media,
                    len(m.text.split()) if m.text else None,
                    "whatsapp",
                    row_key("whatsapp", chat, m.ts.isoformat(), m.sender, m.text),
                )
                for m in msgs
            ]
            con.execute(
                "CREATE OR REPLACE TEMP TABLE _stg_msgs "
                "(ts TIMESTAMPTZ, chat VARCHAR, sender VARCHAR, is_me BOOLEAN, text VARCHAR, "
                " is_media BOOLEAN, word_count INTEGER, source VARCHAR, dedupe_key VARCHAR)"
            )
            if rows:
                con.executemany("INSERT INTO _stg_msgs VALUES (?,?,?,?,?,?,?,?,?)", rows)
            # DECISION: raw_* staging for .txt chats keeps the parsed,
            # pre-dedupe rows tagged with the source file — more debuggable
            # than raw text lines, which are already on disk anyway.
            stage_raw(con, "raw_whatsapp", "SELECT ? AS file, * FROM _stg_msgs", [f.name])
            ins, skip = insert_dedupe(con, "messages", MESSAGE_COLS, "SELECT * FROM _stg_msgs")
            stats.rows_inserted += ins
            stats.rows_skipped += skip
            record_import(con, sha, f, self.id, ins)
        return stats
