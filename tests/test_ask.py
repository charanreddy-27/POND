"""Ask engine with a faked Anthropic client: retries, privacy, schema doc."""

from __future__ import annotations

import pytest

from pond.ask import engine
from pond.ask.engine import AskError, ask
from pond.ask.schema_doc import build_schema_doc


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    """Returns queued replies; records everything it was sent."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self._replies.pop(0)
        return type("Resp", (), {"content": [_FakeBlock(reply)]})()


@pytest.fixture()
def fake_llm(monkeypatch):
    def _install(replies: list[str]) -> _FakeClient:
        client = _FakeClient(replies)
        monkeypatch.setattr(engine, "_make_client", lambda: client)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        return client

    return _install


def test_happy_path(con, cfg, fake_llm):
    client = fake_llm(["```sql\nSELECT 42 AS answer\n```"])
    result = ask("meaning of life?", con, cfg)
    assert result.rows == [(42,)]
    assert result.attempts == 1
    assert "LIMIT" in result.sql.upper()
    sent = str(client.calls[0])
    assert "meaning of life?" in sent


def test_retry_on_bad_sql(con, cfg, fake_llm):
    fake_llm(
        [
            "```sql\nSELECT nope FROM not_a_table\n```",
            "```sql\nSELECT 1 AS ok\n```",
        ]
    )
    result = ask("q", con, cfg)
    assert result.attempts == 2 and result.rows == [(1,)]


def test_guardrail_violation_burns_an_attempt(con, cfg, fake_llm):
    fake_llm(
        [
            "```sql\nDROP TABLE listens\n```",
            "```sql\nSELECT 1 AS ok\n```",
        ]
    )
    result = ask("q", con, cfg)
    assert result.attempts == 2
    assert con.execute("SELECT count(*) FROM listens").fetchone()[0] == 0  # still exists


def test_retries_exhausted(con, cfg, fake_llm):
    fake_llm(["```sql\nDROP TABLE x\n```"] * 3)  # 1 try + 2 retries
    with pytest.raises(AskError, match="attempts"):
        ask("q", con, cfg)


def test_no_api_key(con, cfg, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AskError, match="ANTHROPIC_API_KEY"):
        ask("q", con, cfg)


def test_schema_doc_never_leaks_data(con, cfg):
    con.execute(
        "INSERT INTO messages VALUES (now(), 'Rahul', 'Rahul', false, "
        "'super secret text', false, 3, 'whatsapp', 'k1')"
    )
    con.execute(
        "INSERT INTO transactions VALUES (now(), -100, 'INR', "
        "'UPI-PRIVATE NARRATION-x', 'SWIGGY', 'food_delivery', 'hdfc_savings', 'bank', 'k2')"
    )
    doc = build_schema_doc(con, cfg)
    assert "super secret" not in doc
    assert "PRIVATE NARRATION" not in doc
    # categorical vocab IS shared under the default setting
    assert "food_delivery" in doc and "hdfc_savings" in doc


def test_schema_doc_vocab_none(con, cfg):
    con.execute(
        "INSERT INTO transactions VALUES (now(), -100, 'INR', 'x', 'SWIGGY', "
        "'food_delivery', 'hdfc_savings', 'bank', 'k3')"
    )
    cfg.privacy.vocab_sharing = "none"
    doc = build_schema_doc(con, cfg)
    assert "Known vocabulary" not in doc
