"""SQL guardrails: the LLM's SQL runs locally, but only if it is read-only.

Validated with sqlglot before execution: exactly one statement, SELECT-only,
no DDL/DML/PRAGMA/ATTACH/COPY/SET anywhere in the tree, and no table
functions that touch the filesystem.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp


class GuardrailError(ValueError):
    """Raised when generated SQL fails validation."""


FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.TruncateTable,
    exp.Attach,
    exp.Detach,
    exp.Copy,
    exp.Pragma,
    exp.Set,
    exp.Command,
    exp.Transaction,
    exp.Use,
    exp.Grant,
)

# Table functions / functions that reach outside the database file.
FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_parquet",
    "read_xlsx",
    "read_text",
    "read_blob",
    "glob",
    "getenv",
}

DEFAULT_LIMIT = 500


def validate_sql(sql: str) -> str:
    """Validate and normalize LLM-generated SQL; returns SQL safe to execute.

    Raises GuardrailError when anything but a single read-only SELECT is found.
    Appends LIMIT 500 to un-limited top-level selects.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise GuardrailError(f"SQL does not parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise GuardrailError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise GuardrailError(f"only SELECT queries are allowed, got {type(tree).__name__}")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardrailError(f"forbidden operation: {type(node).__name__}")
        if isinstance(node, exp.Anonymous | exp.Func):
            name = (node.name or "").lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise GuardrailError(f"forbidden function: {name}()")

    if isinstance(tree, exp.Select | exp.Union) and not tree.args.get("limit"):
        tree = tree.limit(DEFAULT_LIMIT)

    return tree.sql(dialect="duckdb")


def extract_sql(text: str) -> str:
    """Pull SQL out of an LLM reply (fenced ```sql block, or the raw text)."""
    if "```" in text:
        chunks = text.split("```")
        for chunk in chunks[1::2]:
            body = chunk.strip()
            if body.lower().startswith("sql"):
                body = body[3:].strip()
            if body:
                return body
    return text.strip().rstrip(";")
