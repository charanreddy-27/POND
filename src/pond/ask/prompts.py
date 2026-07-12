"""System prompt + few-shot examples for the NL->SQL engine (§6)."""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are POND's SQL writer. You translate one plain-English question about the
user's personal data into exactly ONE DuckDB SELECT statement.

Rules:
- Output ONLY the SQL, inside a single ```sql fenced block. No prose.
- One statement. SELECT (with CTEs) only — never INSERT/UPDATE/CREATE/etc.
- DuckDB dialect. date_trunc('week', ts)::DATE gives the Monday of a week.
- Absence questions ("weeks I skipped X", "days without Y") must use the
  `days` / `weeks` spine views — missing activity has no rows of its own.
- Money: amount < 0 is spending; report spend as SUM(-amount) (positive).
- Listening questions: filter ms_played >= 30000.
- Match merchants case-insensitively: merchant ILIKE '%SWIGGY%' or via category.
- Prefer week_start/month_start groupings with readable column aliases.
- Round money to whole units with ROUND(). Keep results under ~100 rows.

The database schema:

{schema}
"""

FEW_SHOTS: list[tuple[str, str]] = [
    (
        "how much did I spend on Swiggy in weeks I skipped gym this year?",
        """\
```sql
WITH weekly_swiggy AS (
  SELECT date_trunc('week', ts)::DATE AS week_start,
         SUM(-amount) AS swiggy_spend
  FROM transactions
  WHERE amount < 0
    AND (merchant ILIKE '%SWIGGY%' OR narration ILIKE '%SWIGGY%')
    AND ts >= date_trunc('year', CURRENT_DATE)
  GROUP BY 1
)
SELECT w.week_start, ROUND(COALESCE(s.swiggy_spend, 0)) AS swiggy_spend
FROM weeks w
LEFT JOIN weekly_swiggy s USING (week_start)
WHERE w.went_to_gym = FALSE
  AND w.week_start >= date_trunc('year', CURRENT_DATE)
  AND w.week_start <= CURRENT_DATE
ORDER BY w.week_start
```""",
    ),
    (
        "top 5 artists by hours listened last month",
        """\
```sql
SELECT artist,
       ROUND(SUM(ms_played) / 3600000.0, 1) AS hours
FROM listens
WHERE ms_played >= 30000
  AND ts >= date_trunc('month', CURRENT_DATE) - INTERVAL 1 MONTH
  AND ts < date_trunc('month', CURRENT_DATE)
GROUP BY artist
ORDER BY hours DESC
LIMIT 5
```""",
    ),
    (
        "days this year with zero messages sent by me",
        """\
```sql
SELECT d.day
FROM days d
LEFT JOIN (
  SELECT DISTINCT ts::DATE AS day FROM messages WHERE is_me
) m USING (day)
WHERE m.day IS NULL
  AND d.day >= date_trunc('year', CURRENT_DATE)
  AND d.day <= CURRENT_DATE
ORDER BY d.day
```""",
    ),
]


def build_system_prompt(schema_doc: str) -> str:
    """Interpolate the schema into the system prompt."""
    return SYSTEM_PROMPT.format(schema=schema_doc)


def build_messages(question: str) -> list[dict[str, str]]:
    """User/assistant few-shot turns followed by the real question."""
    msgs: list[dict[str, str]] = []
    for q, sql in FEW_SHOTS:
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": sql})
    msgs.append({"role": "user", "content": question})
    return msgs


def retry_message(sql: str, error: str) -> str:
    """Follow-up turn asking the model to fix a failing query."""
    return (
        f"That SQL failed.\n\nSQL:\n```sql\n{sql}\n```\n\nError:\n{error}\n\n"
        "Fix it. Reply with ONLY the corrected SQL in a ```sql block."
    )
