"""Guardrails: only a single read-only SELECT ever reaches DuckDB."""

from __future__ import annotations

import pytest

from pond.ask.guardrails import GuardrailError, extract_sql, validate_sql


def test_select_passes():
    out = validate_sql("SELECT 1 AS x")
    assert out.lower().startswith("select")


def test_cte_passes():
    validate_sql("WITH w AS (SELECT 1 AS a) SELECT * FROM w")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO transactions VALUES (1)",
        "UPDATE transactions SET amount = 0",
        "DELETE FROM messages",
        "DROP TABLE listens",
        "CREATE TABLE evil (x INT)",
        "ATTACH 'other.db'",
        "PRAGMA database_list",
        "SET TimeZone='UTC'",
    ],
)
def test_writes_rejected(sql):
    with pytest.raises(GuardrailError):
        validate_sql(sql)


def test_multiple_statements_rejected():
    with pytest.raises(GuardrailError):
        validate_sql("SELECT 1; SELECT 2")


def test_file_functions_rejected():
    with pytest.raises(GuardrailError):
        validate_sql("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_limit_injected():
    out = validate_sql("SELECT ts FROM listens")
    assert "LIMIT 500" in out.upper()


def test_existing_limit_kept():
    out = validate_sql("SELECT ts FROM listens LIMIT 5")
    assert "LIMIT 5" in out.upper() and "500" not in out


def test_extract_sql_from_fence():
    text = "Here you go:\n```sql\nSELECT 1\n```\nhope that helps"
    assert extract_sql(text) == "SELECT 1"


def test_extract_sql_raw():
    assert extract_sql("SELECT 2;") == "SELECT 2"
