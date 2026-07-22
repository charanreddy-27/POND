# Contributing

Thanks for looking. POND is a personal project with a narrow scope, but issues and PRs are welcome —
especially new importers and bank format fixes.

## The one rule

**Never commit personal data.** Not a real export, not a redacted one, not "just for a test". Every
fixture in `tests/fixtures/` is synthetic and hand-written, and it stays that way.

`.gitignore` already covers `*.duckdb`, `/data/`, `.pond/` and `*.pond-tmp.csv`. Run `git status`
before you commit anyway.

## Setup

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run ruff format src tests
```

CI runs exactly those last three. If they pass locally they'll pass there.

## Adding an importer

The shape is fixed by `importers/base.py`. To add one:

1. Subclass `BaseImporter`, set a unique `id`, decorate with `@register`.
2. Implement `detect(path)` — cheap and conservative. Prefer returning `False` over a false positive;
   ambiguity makes the CLI refuse and ask for `--source`.
3. Implement `run(...)` following the five beats every other importer uses:
   hash and check the ledger → stage into a `TEMP TABLE` → `stage_raw()` → `insert_dedupe()` →
   `record_import()`.
4. Pick a **deterministic** `dedupe_key` over the natural key of the row. It must not depend on the
   session timezone — normalise timestamps to a UTC wall-clock string, as `spotify.py` does. Getting
   this wrong means a config change silently re-imports someone's whole history.
5. Import the module in `importers/__init__.py` so `@register` actually runs.
6. Add a synthetic fixture and a test that imports it **twice**, asserting the second run inserts
   zero rows. Idempotency is the invariant most worth guarding.

If your source adds a table, it needs DDL in `db.CURATED_TABLES` **and** a matching description in
`ask/schema_doc.SCHEMA_DOC` — those are hand-synced today, which is a known wart (see
`PROJECT_DEEP_DIVE.md` §8).

## Touching `ask/`

`guardrails.py` is a security boundary. Changes there need a test per attack shape — multi-statement,
comment-smuggled DDL, a forbidden node nested in a CTE, a filesystem function. Loosening a rule needs
a reason in the PR description.

Anything that changes what `schema_doc.py` sends must be visible in `pond schema`. That's the whole
contract: if the command doesn't show it, it doesn't get sent.

## Style

- Ruff, line length 100, and the existing conventions. Run `ruff format` before pushing.
- Type hints on public functions; `from __future__ import annotations` at the top.
- Docstrings say *why*, not *what*.
- `# DECISION:` comments mark non-obvious choices someone would otherwise "fix" later. Add one when
  you do something surprising on purpose.
- Keep `cli.py` thin, and keep its imports lazy — `pond --help` shouldn't pay to import `duckdb` and
  `anthropic`.

## Site changes

`site/` is hand-written HTML and CSS with no build step, and it should stay that way. Themed colours
are declared once with `light-dark()` in `assets/css/styles.css` — add tokens there rather than
hardcoding hex values in a rule. If you add an external asset, update the CSP in `vercel.json` or it
will be blocked.

Check both themes and 375 / 768 / 1024 / 1440 before opening the PR.

## Out of scope

Some things are deliberate omissions rather than missing features:

- **Bank PDF parsing.** Fragile, and CSV/Excel exports exist.
- **A web UI, a server, or multi-user support.** Single-user local tool by design.
- **Sending row data to any remote service.** Non-negotiable — it's the whole point.
