"""NL->SQL pipeline: prompt -> validate -> execute -> render (§6.1).

Privacy: the API call carries the schema doc, optional categorical vocabulary,
and the question. Query RESULTS never leave the machine; execution errors are
sent back only to let the model repair its SQL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import duckdb

from pond.ask.guardrails import GuardrailError, extract_sql, validate_sql
from pond.ask.prompts import build_messages, build_system_prompt, retry_message
from pond.ask.schema_doc import build_schema_doc
from pond.config import Config


class AskError(RuntimeError):
    """Terminal failure of the ask pipeline (no key, retries exhausted, …)."""


@dataclass
class AskResult:
    """Executed query + result set ready for rendering."""

    sql: str
    columns: list[str]
    rows: list[tuple]
    attempts: int


def _make_client():
    """Anthropic client factory — separate for easy monkeypatching in tests."""
    import anthropic

    return anthropic.Anthropic()


def _complete(client, model: str, system: str, messages: list[dict[str, str]]) -> str:
    """One LLM call; returns the text of the reply."""
    resp = client.messages.create(model=model, max_tokens=1500, system=system, messages=messages)
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def ask(question: str, con: duckdb.DuckDBPyConnection, cfg: Config) -> AskResult:
    """Answer a plain-English question with locally-executed SQL.

    Retries up to ``cfg.llm.max_sql_retries`` times, feeding validation or
    execution errors back to the model.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AskError(
            "ANTHROPIC_API_KEY is not set. `pond ask` needs it for NL->SQL "
            "(your data never leaves the machine — only the schema does). "
            "Use `pond sql` for direct queries."
        )
    client = _make_client()
    system = build_system_prompt(build_schema_doc(con, cfg))
    messages = build_messages(question)

    last_error = ""
    for attempt in range(1 + max(0, cfg.llm.max_sql_retries)):
        reply = _complete(client, cfg.llm.model, system, messages)
        raw_sql = extract_sql(reply)
        try:
            sql = validate_sql(raw_sql)
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return AskResult(sql=sql, columns=columns, rows=rows, attempts=attempt + 1)
        except (GuardrailError, duckdb.Error) as e:
            last_error = str(e)
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": retry_message(raw_sql, last_error)})
    raise AskError(
        f"could not produce working SQL after "
        f"{1 + cfg.llm.max_sql_retries} attempts. Last error: {last_error}"
    )


def run_sql(sql: str, con: duckdb.DuckDBPyConnection) -> AskResult:
    """Execute raw user SQL (`pond sql`) — trusted, no guardrails."""
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall() if columns else []
    return AskResult(sql=sql, columns=columns, rows=rows, attempts=1)
