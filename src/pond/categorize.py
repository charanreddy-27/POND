"""Merchant extraction + category rules engine (§5.4 steps 4-5)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from pond.config import category_rules_path

# Ordered regex attempts against `narration`; first capture group wins (§5.4).
# DECISION: the spec's generic UPI pattern (`UPI[-/].*?[-/](...)[-/]`) captures
# the transaction id, not the merchant, and matches 'DR' in UPI/DR/... rows —
# so the DR/CR form is tried first and the generic form anchors the capture
# to the token right after 'UPI-'.
MERCHANT_PATTERNS: list[str] = [
    r"UPI/(?:DR|CR)/\d+/([A-Za-z0-9 .&_]+?)/",  # UPI/DR/6123.../ZOMATO/...
    r"UPI[-/]([A-Za-z0-9 .&_]+?)[-/]",  # UPI-SWIGGY-swiggy@icici-...
    r"POS[ /]\d*[ /]?([A-Za-z0-9 .&*_]+)",  # POS 4123 AMAZON PAY
    r"(?:NEFT|IMPS|RTGS)[-/ ][A-Z0-9]*[-/ ]?([A-Za-z0-9 .&_]+)",
]


def merchant_sql_expr(narration_col: str = "narration") -> str:
    """SQL expression extracting an UPPERCASED merchant from a narration column.

    Cascades through MERCHANT_PATTERNS; no match -> NULL.
    """
    attempts = [
        f"nullif(upper(trim(regexp_extract({narration_col}, '{p}', 1))), '')"
        for p in MERCHANT_PATTERNS
    ]
    return "coalesce(" + ", ".join(attempts) + ")"


def load_rules(path: Path | None = None) -> list[tuple[str, str]]:
    """Load ordered (pattern, category) rules from category_rules.yaml."""
    rules_file = path or category_rules_path()
    if not rules_file.exists():
        return []
    data = yaml.safe_load(rules_file.read_text()) or {}
    out: list[tuple[str, str]] = []
    for rule in data.get("rules", []):
        if isinstance(rule, dict) and rule.get("match") and rule.get("category"):
            out.append((str(rule["match"]), str(rule["category"])))
    return out


def category_sql_expr(rules: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """(CASE expression, params) mapping merchant/narration -> category.

    First matching rule wins; matches merchant when extracted, else narration,
    case-insensitively. Patterns are passed as bind parameters so user-authored
    regexes cannot break the SQL.
    """
    if not rules:
        return "NULL", []
    whens, params = [], []
    for pattern, category in rules:
        cat_lit = category.replace("'", "''")
        whens.append(
            f"WHEN regexp_matches(lower(coalesce(merchant, narration, '')), ?) THEN '{cat_lit}'"
        )
        params.append(pattern.lower())
    return "CASE " + " ".join(whens) + " ELSE NULL END", params


def recategorize_all(con: duckdb.DuckDBPyConnection, rules_path: Path | None = None) -> int:
    """Re-run merchant extraction + categorization over ALL transactions.

    Used by `pond categorize` after the user edits the rules. Returns the
    number of transactions now carrying a category.
    """
    con.execute(f"UPDATE transactions SET merchant = {merchant_sql_expr()}")
    case, params = category_sql_expr(load_rules(rules_path))
    con.execute(f"UPDATE transactions SET category = {case}", params)
    return con.execute("SELECT count(*) FROM transactions WHERE category IS NOT NULL").fetchone()[0]
