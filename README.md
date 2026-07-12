# 🦆 POND — query your life

A **local-first personal data warehouse**. Import Google Takeout, WhatsApp chat
exports, bank statements, and Spotify history into a single DuckDB file on your
own machine, then ask questions in plain English:

```
$ pond ask "how much did I spend on Swiggy in weeks I skipped gym this year?"

  week_start   swiggy_spend
  ──────────   ────────────
  2026-02-16       ₹1,240
  2026-04-06       ₹2,115
  2026-05-25         ₹890
```

**Zero cloud storage.** The only network call is an optional LLM call that sees
your **schema**, never your **data**.

## How it works

```
exports (zip/csv/txt/json) ──► importers ──► DuckDB (~/.pond/pond.duckdb)
                                                   │
                        pond ask "plain English" ──┤──► LLM writes SQL (sees schema only)
                        pond sql  "raw SQL"     ───┘──► DuckDB executes locally ──► Rich table
```

- **Privacy by construction.** Raw data never leaves the machine. `pond ask`
  sends only the schema DDL, the question, and (optionally — see
  `privacy.vocab_sharing`) low-cardinality vocabulary such as category names.
  Generated SQL is validated with sqlglot: single statement, read-only SELECT,
  no file access — before it ever executes.
- **Absence is data.** "Weeks I *skipped* gym" means missing rows, so the
  schema ships `days` / `weeks` calendar-spine views that make gap queries
  trivial (`weeks.went_to_gym`).
- **Imports are cheap and safe.** A file ledger (sha256) plus per-row dedupe
  keys make every import idempotent — re-run anything, any time; overlapping
  exports are a no-op.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd pond
uv sync
uv run pond init --name "YourWhatsAppName" --timezone "Asia/Kolkata"
```

(Or `uv tool install .` to get `pond` on your PATH.)

## Quickstart

```bash
pond import my_spotify_data.zip           # auto-detects the source
pond import takeout-20260101T000000Z.zip
pond import "WhatsApp Chat with Rahul.txt"
pond import stmt.csv --source bank --account hdfc_savings
pond log gym                              # manual escape hatch
pond status                               # what's in the pond
pond sql "SELECT count(*) FROM listens"   # raw SQL, no LLM
export ANTHROPIC_API_KEY=sk-ant-...
pond ask "top 5 artists by hours listened last month" --explain
```

Every import prints rows inserted / duplicates skipped / warnings, and the new
date range of each affected table. Re-importing the same file is always a no-op
(`--force` re-reads a file, but row-level dedupe still prevents duplicates).

## Per-source import instructions

### Spotify

Request your data at [spotify.com/account/privacy](https://www.spotify.com/account/privacy/):
either **Account data** (last year, `StreamingHistory*.json`) or **Extended
streaming history** (lifetime, `Streaming_History_Audio_*.json`). Point
`pond import` at the zip, the folder, or individual files — both flavors land
in the `listens` table. Rows shorter than 30s are kept at import; "listening"
queries filter `ms_played >= 30000`.

### WhatsApp

In a chat: **⋮ → More → Export chat → Without media**. Import the resulting
`.txt` (or a folder of them). Android and iOS formats are both supported —
DD/MM vs MM/DD is auto-resolved by scanning the file (defaults to DD/MM with a
warning if genuinely ambiguous). Set `me.whatsapp_name` in
`~/.pond/config.yaml` so `messages.is_me` is tagged correctly.

### Google Takeout

At [takeout.google.com](https://takeout.google.com), select **My Activity
(Search, YouTube), Fit, and Calendar**, and set the My Activity format to
**JSON** (not HTML). Import the zip or the extracted `Takeout/` folder — each
product is imported if present; missing ones are reported, not fatal.

- Search → `searches` (only "Searched for …" entries)
- YouTube → `youtube_watches` (ads skipped, deleted videos keep NULL channel)
- Fit dailies → `daily_metrics`; Fit sessions → `activities`
- Calendar → `calendar_events` (recurring events: master only, no expansion)

> ⚠️ Location history moved on-device in 2024 and is not in most new Takeouts —
> out of scope.

### Bank statements

**CSV/Excel netbanking exports only** — download the "Excel/CSV" statement
format from netbanking (every major Indian bank offers it). **PDF statements
are out of scope.**

```bash
pond import stmt.csv --source bank --account hdfc_savings
```

The **first** import of a new bank format asks a few interactive questions
(which column is the date? narration? single signed amount or separate
debit/credit?) and saves the answers to `~/.pond/bank_profiles.yaml` — after
that, imports of that bank are fully automatic. Amounts are normalized to
**negative = money out**; ₹, commas, and Cr/Dr suffixes are handled.

Merchants are extracted from UPI/POS/NEFT narrations and categorized via
`~/.pond/category_rules.yaml` (ordered regex rules, first match wins). Edit the
rules, then re-apply to all existing transactions with:

```bash
pond categorize
```

### Manual log

Some signals have no data trail:

```bash
pond log gym                                # today, type=strength_training
pond log badminton --date 2026-07-09 --minutes 60
```

This is what makes "weeks I skipped gym" answerable even before any Google Fit
import exists.

## `pond ask`

Needs `ANTHROPIC_API_KEY`. The model (default `claude-sonnet-4-6`, configurable
in `~/.pond/config.yaml`) receives the schema doc and your question, writes one
DuckDB SELECT, and POND validates + executes it **locally**. Failures are fed
back for up to `llm.max_sql_retries` repair attempts. Use `--explain` to see
the SQL, `--format csv|json` for machine output.

Privacy knob in `config.yaml`:

```yaml
privacy:
  vocab_sharing: "categorical"   # none | categorical
```

`categorical` shares distinct values of transactions.category,
transactions.account, and activities.activity_type (closed, low-cardinality
sets) so questions like "spend on food delivery" map to exact values. `none`
shares schema only. Narrations, messages, queries, titles — never.

## Data model (curated tables)

| table | grain | highlights |
|---|---|---|
| `transactions` | one bank row | `amount` (negative = spend), `merchant`, `category` |
| `messages` | one WhatsApp message | `chat`, `sender`, `is_me`, `word_count` |
| `listens` | one Spotify play | `ms_played`, `skipped` |
| `activities` | one workout/session | `activity_type`, `duration_min` |
| `daily_metrics` | one day (Fit) | `steps`, `distance_m`, `calories` |
| `searches` / `youtube_watches` / `calendar_events` | one event | |
| `days` / `weeks` (views) | calendar spine | `week_start`, `went_to_gym` |

All timestamps are `TIMESTAMPTZ` localized to `me.timezone`. Raw source files
are also staged as-is into `raw_*` tables for debugging. The `_imports` ledger
records every processed file by sha256.

## Development

```bash
uv sync
uv run pytest        # all tests run against synthetic fixtures in tests/fixtures/
uv run ruff check src tests
```

No real personal data is required or committed anywhere; never commit anything
under `~/.pond/` or `data/`.

## MVP limitations

- Bank PDFs, Google location history: out of scope.
- Recurring calendar events are stored as the master event only.
- `.xls`/`.xlsx` statements are converted via DuckDB's Excel reader
  (best-effort); re-export as CSV if that fails.
- CLI only — no web UI, dashboards, or daemons.
