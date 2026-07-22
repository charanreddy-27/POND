# Changelog

Notable changes to POND. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Website** (`site/`) — static marketing site, zero dependencies and no build step, deployed to
  Vercel. Landing page with an animated terminal replay of real command shapes, plus `/about` and
  `/about-project`. Light and dark themes, `prefers-reduced-motion` respected, and it degrades to
  plain readable HTML without JavaScript.
- `vercel.json` — static output config, `cleanUrls`, immutable asset caching, and security headers
  including a CSP.
- `site/assets/og.png`, `favicon.svg`, `robots.txt`, `sitemap.xml`, and Open Graph / Twitter card
  metadata on every page.
- Documentation: `PROJECT_DEEP_DIVE.md`, `INTERVIEW_PREP.md`, `DEPLOYMENT.md`, `CONTRIBUTING.md`,
  and this changelog.

### Fixed

- **Cross-platform encoding bug.** Several call sites read and wrote text without an explicit
  encoding, so they used the platform locale — on Windows (cp1252) that corrupted any non-ASCII
  content in POND's own config files, and mangled the `U+200E` marks in the WhatsApp iOS test
  fixture badly enough to drop a message. All text I/O in `config.py`, `categorize.py`,
  `importers/bank.py` and the test suite is now explicitly UTF-8, and YAML is written with
  `allow_unicode=True` so a `₹` in a category rule round-trips readably. This fixed
  `test_ios_parse`, which failed on any non-UTF-8 locale.
  *(The importers were already correct — they always passed `encoding="utf-8"`.)*

### Changed

- Rewrote `README.md` around the demo and the privacy boundary rather than the install steps.

## [0.1.0]

Initial build.

### Added

- **Warehouse.** DuckDB at `~/.pond/pond.duckdb` with eight curated tables — `transactions`,
  `messages`, `listens`, `activities`, `daily_metrics`, `searches`, `youtube_watches`,
  `calendar_events` — plus `raw_*` staging tables and the `_imports` ledger. Migrations run on every
  connect.
- **Calendar spine views.** `days` and `weeks` manufacture the rows that gaps don't have, so
  "weeks I skipped gym" is a `WHERE` clause instead of an anti-join.
- **Importers.** Spotify (both export flavours), WhatsApp (Android and iOS `.txt`, with automatic
  DD/MM vs MM/DD resolution), Google Takeout (Search, YouTube, Fit dailies and sessions, Calendar),
  and bank CSV/XLSX with learned per-bank profiles. Zip archives are extracted automatically and the
  source is auto-detected.
- **Two-layer idempotency.** sha256 file ledger plus a per-row `UNIQUE` dedupe key, so re-importing
  anything — including overlapping exports — is a no-op.
- **`pond ask`.** Natural language to SQL via Claude, with error-feedback repair retries.
- **Privacy boundary.** Only schema DDL and, optionally, the distinct values of three whitelisted
  low-cardinality columns are sent. `pond schema` prints the exact payload using the same code path
  as the API call.
- **SQL guardrails.** sqlglot AST validation before execution: single statement, `SELECT` only, no
  DDL/DML/`PRAGMA`/`ATTACH`/`COPY`, no filesystem-reaching functions, automatic `LIMIT 500`.
- **CLI.** `init`, `import`, `log`, `categorize`, `sql`, `ask`, `status`, `schema`, `version` —
  Typer with Rich output and `--format table|csv|json`.
- **Merchant extraction and categorisation.** Ordered regex cascade over UPI/POS/NEFT narrations,
  with user-editable rules in `~/.pond/category_rules.yaml` and `pond categorize` to re-apply them.
- 56 tests against synthetic fixtures, ruff lint and format, GitHub Actions CI.
