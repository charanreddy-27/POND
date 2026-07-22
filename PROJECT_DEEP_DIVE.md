# POND — deep dive

Architecture, data flow, and the parts that were genuinely hard. If you want the pitch, read
[`README.md`](README.md). If you want to know *why* it's shaped like this, you're in the right file.

---

## 1. The one-paragraph version

POND is a single-user, local-first data warehouse. Importers normalise messy consumer exports into
eight curated DuckDB tables plus two calendar-spine views. A natural-language layer sends **only the
schema** to an LLM, receives SQL, validates that SQL on its parse tree, and executes it locally. The
privacy boundary is the architecture, not a setting.

---

## 2. Folder structure

```
src/pond/
├─ cli.py                  Typer app. Thin — every command delegates, imports are lazy
│                          so `pond --help` doesn't pay for duckdb + anthropic.
├─ config.py               Pydantic config models; ~/.pond resolution (POND_HOME override).
├─ db.py                   Connection factory, curated DDL, ledger DDL, spine views, migrations.
├─ ledger.py               sha256 file hashing + `_imports` helpers + row_key().
├─ categorize.py           Merchant regex cascade + category rules → SQL expressions.
├─ render.py               Rich table / CSV / JSON output + import summaries.
├─ ask/
│  ├─ engine.py            The NL→SQL loop: prompt → validate → execute → repair.
│  ├─ guardrails.py        sqlglot AST validation. The security boundary.
│  ├─ prompts.py           System prompt + retry framing.
│  └─ schema_doc.py        Builds the LLM payload. The privacy boundary.
└─ importers/
   ├─ base.py              BaseImporter ABC, REGISTRY, detection, insert_dedupe, stage_raw.
   ├─ spotify.py           Both export flavours → listens.
   ├─ whatsapp.py          Android + iOS .txt parsing → messages.
   ├─ bank.py              CSV/XLSX + learned per-bank profiles → transactions.
   ├─ takeout.py           Dispatcher over the four Takeout sub-importers.
   └─ takeout_{search,youtube,fit,calendar}.py

rules/category_rules.yaml  Spec mirror of the starter rules.
src/pond/data/             The canonical packaged copy (so `uv tool install .` works).
tests/                     56 tests, synthetic fixtures only.
site/                      Zero-dependency static marketing site (deployed to Vercel).
```

**Why two copies of `category_rules.yaml`?** The packaged copy under `src/pond/data/` is what gets
installed with the wheel and copied into `~/.pond` on `pond init`. The repo-root `rules/` copy exists
for spec conformance. `config._starter_rules_path()` always resolves the packaged one — the comment
in that function says so, because it's exactly the kind of thing that looks like a bug six months
later.

---

## 3. Data flow

### 3.1 Import

```
path ──► maybe_extract_zip() ──► detect_importers() ──► Importer.run()
                                       │
                                       └─ bank is the lowest-priority catch-all:
                                          dropped whenever anything else also matches
                                       └─ still ambiguous? exit(1) and tell the user
                                          to pass --source
```

Inside `run()`, every importer follows the same five beats:

1. `file_sha256(f)` → `already_imported()`? Skip (unless `--force`).
2. Parse/stage into a `TEMP TABLE _stg_*`.
3. `stage_raw()` — append the raw rows to a persistent `raw_*` table. **Best-effort by design:**
   schema drift between export versions must never fail an import, so `duckdb.Error` is swallowed
   there and the curated load reads from the source directly.
4. `insert_dedupe()` — the real load.
5. `record_import()` — write the ledger entry.

The importer returns an `ImportStats` (inserted / skipped / files skipped / warnings / tables
touched), which `cli.import_` turns into a Rich summary including the *new* date range of every
table the import touched. That last part matters more than it sounds: it's the fastest way to
notice you imported the wrong year.

### 3.2 Query

```
question ─┐
          ├─► build_schema_doc(con, cfg) ──► system prompt ──► Claude ──► reply
config  ──┘                                                                │
                                                          extract_sql() ◄──┘
                                                                │
                                              validate_sql() ── sqlglot AST walk
                                                                │
                                     ┌── GuardrailError / duckdb.Error ──┐
                                     │                                    ▼
                                     │                          retry_message() ──► loop
                                     ▼                          (max_sql_retries, default 2)
                                con.execute() ──► AskResult ──► Rich / CSV / JSON
```

Failures feed the **error text** back to the model — never the data. If retries are exhausted,
`AskError` carries the last error and the CLI exits 1.

---

## 4. The three hard parts

### 4.1 Making the privacy claim inspectable

The naive version of this project sends rows to an LLM. POND sends `schema_doc.SCHEMA_DOC` — a
hand-written DDL description with column comments — plus, when
`privacy.vocab_sharing == "categorical"`, distinct values from exactly three whitelisted columns:

```python
VOCAB_COLUMNS = [
    ("transactions", "category"),
    ("transactions", "account"),
    ("activities", "activity_type"),
]
```

All three are closed, low-cardinality sets, capped at 50 values each. The point of sharing them is
that "spend on food delivery" needs to map to the literal string `food_delivery`; without the
vocabulary the model guesses and the query silently returns zero rows.

**The design decision that makes this trustworthy:** `pond schema` calls the *same*
`build_schema_doc(con, cfg)` that `ask()` calls. There is no second, documentation-flavoured
implementation to drift out of sync. What the command prints is definitionally what the API call
sends.

The trade-off: `SCHEMA_DOC` is a hand-maintained string rather than introspected from
`information_schema`. Introspection would auto-track schema changes, but it would also happily leak
a column you didn't mean to describe, and it can't carry the prose conventions ("spending is
`SUM(-amount)` over `amount < 0` rows") that do most of the work in getting correct SQL on the first
try. Hand-maintained wins here; the cost is a comment in `db.py` reminding you to mirror changes.

### 4.2 Sandboxing generated SQL

`ask/guardrails.py` is the security boundary. It runs **before** anything reaches DuckDB:

```python
statements = sqlglot.parse(sql, read="duckdb")   # ParseError → GuardrailError
if len(statements) != 1: reject                  # kills "SELECT 1; DROP TABLE …"
if not isinstance(tree, exp.Query): reject       # must be a SELECT/UNION at the top
for node in tree.walk():
    if isinstance(node, FORBIDDEN_NODES): reject # Insert/Update/Delete/Create/Drop/Alter/
                                                 # Merge/Truncate/Attach/Detach/Copy/Pragma/
                                                 # Set/Command/Transaction/Use/Grant
    if func_name in FORBIDDEN_FUNCTIONS: reject  # read_csv/read_json/read_parquet/read_xlsx/
                                                 # read_text/read_blob/glob/getenv
if no top-level limit: tree = tree.limit(500)
return tree.sql(dialect="duckdb")                # re-serialised from the validated tree
```

Three things worth calling out:

- **It walks the whole tree**, not just the root. A forbidden node hiding inside a CTE or a
  subquery is still a forbidden node.
- **`exp.Command` is in the blocklist.** That's sqlglot's catch-all for statements it doesn't model
  natively, which is precisely where a novel DuckDB verb would hide.
- **The returned SQL is re-serialised from the validated AST**, not the original string. What gets
  executed is what got validated — no room for a discrepancy between the two.

`pond sql` deliberately skips all of this (`run_sql()`), because that path is the *user* typing SQL
at their own database. Trusting the human and distrusting the model is the correct asymmetry.

### 4.3 Idempotency in two layers

| Layer | Mechanism | Catches |
|---|---|---|
| File | `sha256` → `_imports` table | Re-running `pond import` on the same file |
| Row | `dedupe_key VARCHAR NOT NULL UNIQUE` | Two *different* files containing the same rows |

The row layer is the one you only learn you need after Spotify's "account data" and "extended
history" exports both hand you last March. `insert_dedupe()` handles both directions:

```sql
INSERT INTO {table} ({cols})
SELECT {cols} FROM (
    SELECT *, row_number() OVER (PARTITION BY dedupe_key) AS _rn
    FROM ({select_sql})
) s
WHERE s._rn = 1                                        -- dupes *within* this batch
  AND s.dedupe_key NOT IN (SELECT dedupe_key FROM {table})  -- dupes vs. existing rows
```

Keys are deterministic sha256 over the natural key. Two subtleties:

- **Timezone independence.** The Spotify key normalises the timestamp to a UTC wall-clock string
  (`strftime(ts AT TIME ZONE 'UTC', …)`) so the key never depends on `SET TimeZone`. Otherwise
  changing your config timezone would silently re-import your entire history.
- **Two implementations, one definition.** SQL-side importers build the key with DuckDB's
  `sha256(concat_ws(…))`; Python-side ones (`pond log`, WhatsApp) use `ledger.row_key()`. Both
  canonicalise `None`/NULL to `''` and join with `|`.

---

## 5. Absence as a first-class concept

The original question — "weeks I *skipped* gym" — is unanswerable against fact tables alone, because
skipping produces no row. `db.SPINE_VIEWS` manufactures the missing rows:

```sql
CREATE OR REPLACE VIEW days AS
SELECT CAST(d AS DATE) AS day,
       date_trunc('week', d)::DATE AS week_start,
       date_trunc('month', d)::DATE AS month_start
FROM generate_series(<earliest data or 365 days ago>, CURRENT_DATE, INTERVAL 1 DAY) t(d);

CREATE OR REPLACE VIEW weeks AS
SELECT week_start,
       EXISTS (SELECT 1 FROM activities a
               WHERE date_trunc('week', a.ts_start)::DATE = w.week_start
                 AND a.activity_type IN ('strength_training','gym','weightlifting'))
         AS went_to_gym
FROM (SELECT DISTINCT week_start FROM days) w;
```

`weeks.went_to_gym` is a deliberate concession to ergonomics: it hard-codes gym-ish activity types
so the model doesn't have to guess synonyms. It's the one place in the schema where a personal
opinion is baked in, and it earns its keep — "weeks I skipped gym" becomes
`WHERE went_to_gym = FALSE` instead of an anti-join the model gets wrong about a third of the time.

Views are `CREATE OR REPLACE`'d on **every** `connect()`. They're cheap, and it guarantees they can
never drift from the code that defines them.

`pond log` exists for the same reason. Google Fit only knows about workouts you told it about; the
manual escape hatch means `weeks.went_to_gym` is real from day one. Manual entries land at **12:00
local** so they bucket into the right local day and week regardless of timezone arithmetic.

---

## 6. Bank statements: the ugly one

Indian netbanking CSVs disagree about everything: where the header row is, how many junk preamble
lines precede it, date format (`%d/%m/%Y` vs `%d-%b-%Y` vs `%Y-%m-%d`), whether the amount is one
signed column or separate debit/credit columns, and whether `Cr`/`Dr` suffixes are carrying the sign.

**The design:** don't guess. `BankProfile` is a Pydantic model recording the answers to four
questions, saved to `~/.pond/bank_profiles.yaml` and matched on a **sorted, lowercased header
signature** rather than a filename or bank name. That matching choice is what makes it survive banks
shuffling their preamble rows — `find_profile()` re-derives the header row index per file:

```python
for i, row in enumerate(rows):          # first 30 rows
    if _signature(row) == p.header_signature:
        return p, i                     # header found at row i *this time*
```

**Amount normalisation** is generated SQL (`_amount_expr`), not Python, so it runs in DuckDB over the
whole file at once:

```sql
CASE WHEN regexp_matches(upper(coalesce("col",'')), 'DR\.?\s*$') THEN -abs(num)
     WHEN regexp_matches(upper(coalesce("col",'')), 'CR\.?\s*$') THEN  abs(num)
     ELSE num END
-- where num = TRY_CAST(regexp_replace("col", '[^0-9.-]', '', 'g') AS DECIMAL(12,2))
```

`TRY_CAST` means a garbage row yields NULL and is filtered out rather than exploding the import.
Opening/closing balance lines are dropped by a narration regex.

**Merchant extraction** is an ordered regex cascade in `categorize.MERCHANT_PATTERNS`, first capture
wins. There's a `# DECISION:` comment on it worth repeating: the obvious generic UPI pattern
(`UPI[-/].*?[-/](...)[-/]`) captures the *transaction id*, not the merchant, and matches the literal
`DR` in `UPI/DR/...` rows. So the DR/CR form is tried first and the generic form anchors its capture
to the token immediately after `UPI-`.

**Category rules** are user-authored regexes, which means they're untrusted input to SQL generation.
`category_sql_expr()` passes every pattern as a **bind parameter** and only interpolates the
category *name* (with quote-escaping). A user can write a rule that matches nothing; they can't
write one that breaks the query.

---

## 7. Testing strategy

56 tests, all against **synthetic fixtures** in `tests/fixtures/`. No real personal data has ever
been in this repo, which is a constraint that shaped the code: every importer takes a path, so every
importer is testable without a network call or a real export.

`tests/conftest.py` points `POND_HOME` at a `tmp_path`, and `config.load_config()` has a matching
rule — if `POND_HOME` is set and `paths.db` is still the default, the database moves inside
`POND_HOME`. That one branch is what makes the suite fully self-contained; without it a test run
would happily open your actual warehouse.

The LLM is monkeypatched at `engine._make_client()` — a deliberately tiny seam that exists purely so
the ask pipeline is testable end-to-end without an API key.

**A portability bug the suite caught (July 2026):** several call sites used `Path.read_text()` with
no `encoding=`, which resolves to the platform's locale encoding. On Windows (cp1252) that mangled
the `U+200E` marks in the WhatsApp iOS fixture and silently dropped a message. Fixed by making
every text read and write explicitly UTF-8 in `config.py`, `categorize.py`, `bank.py` and the
tests. Worth noting that the *importer* was already correct — it always passed
`encoding="utf-8", errors="replace"`. The bug was in the code that reads POND's own config files,
where a `₹` in a category rule would have corrupted on any non-UTF-8 locale.

---

## 8. Trade-offs I'd defend, and things I'd change

**Defend:**

- **DuckDB over SQLite.** Window functions, `generate_series`, `TIMESTAMPTZ`, `read_json` with an
  explicit column schema, and an Excel reader — all in-process. The spine views alone would have
  been meaningful Python in SQLite.
- **Guardrails as an AST walk.** Non-negotiable once you accept that model output is untrusted input.
- **Interactive-once bank mapping.** Correctness over cleverness on financial data.
- **Lazy imports in `cli.py`.** `pond --help` shouldn't import `anthropic` and `duckdb`.

**Change:**

- **`SCHEMA_DOC` should be generated from `CURATED_TABLES` with an explicit allowlist**, so the
  privacy payload and the real schema can't drift. Right now a `db.py` change requires a manual
  `schema_doc.py` edit, and only a human catches it.
- **No incremental re-categorisation.** `pond categorize` rewrites every row. Fine at 5k
  transactions, wasteful at 500k.
- **`stage_raw()` swallows all `duckdb.Error`.** Right call for schema drift, too broad for a real
  bug — it should warn into `ImportStats` rather than passing silently.
- **`weeks.went_to_gym` is hard-coded.** It should read the activity synonyms from config so the view
  isn't opinionated about *your* gym.
- **Single-user by construction.** There's no tenancy concept anywhere. That's correct for the
  product and would be a rewrite if it ever stopped being.

---

## 9. The website

`site/` is deliberately the opposite of a modern frontend: three HTML files, one CSS file, one JS
file, no dependencies, no build step, no framework. It deploys as a Vercel static output directory.

- Design tokens are CSS custom properties, with a `[data-theme]` override layered over
  `prefers-color-scheme` so the toggle wins in both directions.
- The hero terminal replays real command shapes with synthetic data (labelled as such). Under
  `prefers-reduced-motion` it renders the final frame immediately; it pauses via
  `IntersectionObserver` when scrolled away and on `visibilitychange`.
- The contact form has no backend — it validates client-side and hands off to `mailto:`. Adding a
  form-analytics vendor to a page about not sending your data anywhere felt like a bad joke.

See [`DEPLOYMENT.md`](DEPLOYMENT.md).
