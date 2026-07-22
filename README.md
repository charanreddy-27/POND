<div align="center">

<img src="site/assets/favicon.svg" width="72" height="72" alt="POND" />

# POND — query your life

**A local-first personal data warehouse.** Your exports go into one DuckDB file on your own
machine. Your questions go in as plain English. The LLM sees your **schema** — never your **data**.

[**Live site**](https://pond-cli.vercel.app) · [How it was built](https://pond-cli.vercel.app/about-project) · [Deep dive](PROJECT_DEEP_DIVE.md) · [Deploy](DEPLOYMENT.md)

[![CI](https://github.com/charanreddy-27/pond/actions/workflows/ci.yml/badge.svg)](https://github.com/charanreddy-27/pond/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![DuckDB](https://img.shields.io/badge/engine-DuckDB-yellow)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

</div>

---

```console
$ pond ask "how much did I spend on Swiggy in weeks I skipped gym this year?"

  week_start   swiggy_spend
  ──────────   ────────────
  2026-02-16       1,240.00
  2026-04-06       2,115.00
  2026-05-25         890.00

3 rows · swiggy_spend: total 4,245.00 · avg 1,415.00
```

That question needs three sources joined on a calendar, and the interesting half of it —
*skipped* — is about rows that **don't exist**. No app's annual wrap-up is going to answer it.

> [!NOTE]
> **Screenshot / GIF goes here.** Record a terminal session of `pond import` → `pond status` →
> `pond ask --explain` and drop it in as `docs/demo.gif`, then replace this block with
> `![POND demo](docs/demo.gif)`.

## Why this exists

Spotify knows what you played at 2am. Your bank knows what you ordered afterwards. Google Fit knows
you didn't train that week. Every one of them shows you a beautiful chart about one slice of your
life, and none of them talk to each other.

The two obvious options are a spreadsheet that dies around row 40,000, or a SaaS that would like you
to upload your bank statements and trust its privacy policy. POND is the third option.

## Features

- **The LLM sees the schema, not the data.** `pond ask` sends the schema DDL, your question, and
  (optionally) low-cardinality vocabulary like category names. Run **`pond schema`** to print the
  exact payload before you send anything — it's the same code path, so the claim can't drift from
  the implementation.
- **Generated SQL is sandboxed.** Every query is parsed with sqlglot and validated on the AST: one
  statement, `SELECT` only, no DDL/DML/`PRAGMA`/`ATTACH`/`COPY`, and no `read_csv`-style functions
  that reach into the filesystem. Un-limited selects get a `LIMIT 500`.
- **Absence is data.** `days` and `weeks` calendar-spine views manufacture the rows that gaps don't
  have, so "weeks I *skipped* gym" is a `WHERE went_to_gym = FALSE` instead of a clever anti-join.
- **Imports are idempotent, twice over.** A sha256 file ledger skips files you've already processed;
  a per-row `dedupe_key` catches the overlap when two exports cover the same March. Re-run anything.
- **Banks are learned once.** The first import of an unknown format asks four questions, saves a
  profile keyed on the header signature, and never asks again.
- **An escape hatch for signals with no data trail.** `pond log gym` writes one row and moves on.
- **No LLM required.** `pond sql` runs raw SQL locally with nothing in the loop, and
  `--format csv|json` pipes into whatever you were going to do next.

## Architecture

```
exports (zip/csv/txt/json) ──► importers ──► DuckDB (~/.pond/pond.duckdb)
                                                   │
                        pond ask "plain English" ──┤──► LLM writes SQL (schema only)
                        pond sql  "raw SQL"     ───┘──► sqlglot validates ──► DuckDB
                                                        executes LOCALLY ──► Rich table
```

Everything above is on your machine except the one labelled hop. Full walkthrough in
[`PROJECT_DEEP_DIVE.md`](PROJECT_DEEP_DIVE.md).

## Tech stack

| | |
|---|---|
| **DuckDB** | Analytics engine in a single file. Window functions and `generate_series` without a server. |
| **sqlglot** | Real SQL parser — turns "validate the model's SQL" into an AST walk, not a regex. |
| **Anthropic Claude** | Schema in, SQL out. Configurable model, bounded retries, entirely optional. |
| **Typer + Rich** | Typed signatures become a CLI; output you'd want to read at 1am. |
| **Pydantic** | Config and bank profiles are validated models, not hopeful dictionaries. |
| **uv** | Locked, reproducible envs in seconds. |
| **pytest · ruff · Actions** | 56 tests on synthetic fixtures; lint and format enforced in CI. |

## Run it locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/charanreddy-27/pond && cd pond
uv sync
uv run pond init --name "YourWhatsAppName" --timezone "Asia/Kolkata"
```

Or `uv tool install .` to get `pond` on your PATH.

```bash
pond import my_spotify_data.zip           # auto-detects the source
pond import takeout-20260101T000000Z.zip
pond import "WhatsApp Chat with Rahul.txt"
pond import stmt.csv --source bank --account hdfc_savings
pond log gym                              # manual escape hatch
pond status                               # what's in the pond
pond schema                               # exactly what `pond ask` would send
pond sql "SELECT count(*) FROM listens"   # raw SQL, no LLM

export ANTHROPIC_API_KEY=sk-ant-...
pond ask "top 5 artists by hours listened last month" --explain
```

Every import prints rows inserted / duplicates skipped / warnings, plus the new date range of each
affected table. Re-importing the same file is always a no-op.

### The website

`site/` is a zero-dependency static site (hand-written HTML + CSS, no build step). Preview it with
any static server:

```bash
python -m http.server 4173 --directory site
```

## Per-source import notes

<details>
<summary><b>Spotify</b></summary>

Request your data at [spotify.com/account/privacy](https://www.spotify.com/account/privacy/) —
either **Account data** (last year, `StreamingHistory*.json`) or **Extended streaming history**
(lifetime, `Streaming_History_Audio_*.json`). Point `pond import` at the zip, the folder, or
individual files; both flavours land in `listens`. Rows shorter than 30s are kept at import;
"listening" queries filter `ms_played >= 30000`.
</details>

<details>
<summary><b>WhatsApp</b></summary>

In a chat: **⋮ → More → Export chat → Without media**. Import the resulting `.txt` (or a folder of
them). Android and iOS formats are both supported — DD/MM vs MM/DD is auto-resolved by scanning the
file, defaulting to DD/MM with a warning if genuinely ambiguous. Set `me.whatsapp_name` in
`~/.pond/config.yaml` so `messages.is_me` is tagged correctly.
</details>

<details>
<summary><b>Google Takeout</b></summary>

At [takeout.google.com](https://takeout.google.com), select **My Activity (Search, YouTube), Fit,
and Calendar**, and set the My Activity format to **JSON** (not HTML). Import the zip or the
extracted `Takeout/` folder — each product is imported if present; missing ones are reported, not
fatal.

- Search → `searches` (only "Searched for …" entries)
- YouTube → `youtube_watches` (ads skipped, deleted videos keep a NULL channel)
- Fit dailies → `daily_metrics`; Fit sessions → `activities`
- Calendar → `calendar_events` (recurring events: master only, no expansion)

⚠️ Location history moved on-device in 2024 and is not in most new Takeouts — out of scope.
</details>

<details>
<summary><b>Bank statements</b></summary>

**CSV/Excel netbanking exports only.** PDF statements are out of scope.

```bash
pond import stmt.csv --source bank --account hdfc_savings
```

The **first** import of a new bank format asks a few interactive questions (which column is the
date? the narration? one signed amount or separate debit/credit?) and saves the answers to
`~/.pond/bank_profiles.yaml`. After that, imports of that bank are fully automatic. Amounts are
normalised to **negative = money out**; ₹, commas, and `Cr`/`Dr` suffixes are handled.

Merchants are extracted from UPI/POS/NEFT narrations and categorised via
`~/.pond/category_rules.yaml` (ordered regex rules, first match wins). Edit the rules, then re-apply
to all existing transactions with `pond categorize`.
</details>

## Data model

| Table | One row is | Worth knowing |
|---|---|---|
| `transactions` | one bank line | `amount` (negative = spend), `merchant`, `category` |
| `messages` | one WhatsApp message | `chat`, `sender`, `is_me`, `word_count` |
| `listens` | one Spotify play | `ms_played` — filter `>= 30000` for real listens |
| `activities` | one workout/session | `activity_type`, `duration_min`; also fed by `pond log` |
| `daily_metrics` | one day (Fit) | `steps`, `distance_m`, `calories` |
| `searches` / `youtube_watches` / `calendar_events` | one event | |
| `days` / `weeks` *(views)* | a calendar spine | `week_start`, `went_to_gym` |

All timestamps are `TIMESTAMPTZ` localised to `me.timezone`. Raw source rows are also staged as-is
into `raw_*` tables for debugging. The `_imports` ledger records every processed file by sha256.

## Privacy

```yaml
# ~/.pond/config.yaml
privacy:
  vocab_sharing: "categorical"   # none | categorical
```

`categorical` shares distinct values of `transactions.category`, `transactions.account`, and
`activities.activity_type` — closed, low-cardinality sets — so "spend on food delivery" maps to
exact values. `none` shares schema only. Narrations, messages, search queries and video titles are
**never** in the payload under any setting.

Run `pond schema` at any time to see, verbatim, the full payload `pond ask` would send.

## Development

```bash
uv sync
uv run pytest                        # synthetic fixtures only — no real data anywhere
uv run ruff check src tests
uv run ruff format --check src tests
```

Never commit anything under `~/.pond/` or `data/`. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Known limitations

- Bank PDFs and Google location history: out of scope.
- Recurring calendar events are stored as the master event only.
- `.xls`/`.xlsx` statements go through DuckDB's Excel reader (best-effort); re-export as CSV if that
  fails.
- CLI only — no dashboards or daemons. The website is marketing, not a UI.

---

## About the developer

**Chanda Charan Reddy** — AI & Automation Engineer, Bangalore.

I ship production LLM systems: a Springer-published model that reads chest X-rays well enough for a
radiologist to take seriously, document pipelines that run themselves, and this. Before all that I
wrote real-time control code for jet engines at DRDO, where a millisecond of lag isn't a bug — it's
a flameout.

POND is one project. There are a good few more over at **[charanreddy.dev](https://www.charanreddy.dev)**.

[Portfolio](https://www.charanreddy.dev) · [LinkedIn](https://www.linkedin.com/in/chandacharanreddy/) · [GitHub](https://github.com/charanreddy-27) · [Book a call](https://cal.com/charanreddy-27/30min) · [ORCID](https://orcid.org/0009-0003-2414-6717)

**Want to build something — or break something interesting?**
[Let's talk.](https://cal.com/charanreddy-27/30min)

## License

MIT — see [`pyproject.toml`](pyproject.toml).
