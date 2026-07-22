# POND — interview prep

Rehearsal material. Read it out loud once; the timings below assume you're speaking, not reading.

---

## The 30-second elevator pitch

> POND is a local-first personal data warehouse. You export your data from Google, WhatsApp, your
> bank and Spotify, and POND normalises all of it into one DuckDB file on your own laptop. Then you
> ask questions in plain English — "how much did I spend on food delivery in weeks I skipped the
> gym?" — and it answers.
>
> The interesting constraint is privacy. The LLM never sees a row of your data. It gets the schema
> and your question, it writes SQL, and my machine executes it. And that's not a claim in a README —
> there's a `pond schema` command that prints the exact payload before you send anything.

**If they only remember one thing, make it:** *the model sees the schema, never the data.*

---

## The 2-minute walkthrough

Four beats. Don't rush the third one — it's the interesting part.

**1. The problem (20s)**

> Nine different apps each knew a slice of my year and none of them talked to each other. Spotify
> knows what I played at 2am; my bank knows what I ordered afterwards; Google Fit knows I didn't
> train that week. Every one gives you a pretty annual wrap-up about its own silo. None of them can
> answer a cross-source question. And the question I actually had — "what did I spend on delivery in
> the weeks I skipped the gym" — is worse than cross-source, because the interesting half of it is
> about weeks where *nothing happened*. There are no rows for skipping the gym.

**2. The shape of the system (30s)**

> So: importers normalise messy consumer exports — zips, CSVs, JSON, WhatsApp text files — into eight
> curated DuckDB tables. DuckDB because I wanted window functions, `generate_series` and real
> timestamp handling in a single file with no server. On top of that there's a natural-language layer
> that turns English into SQL, and a CLI over both.

**3. The two constraints that shaped everything (60s)**

> The first is privacy. The obvious way to build this is to send data to a model, and I wasn't going
> to do that with my own bank statements. So the payload is the schema DDL plus, optionally, the
> distinct values of three low-cardinality columns — category names, account names, activity types —
> because "food delivery" needs to map to the literal string `food_delivery` or the query silently
> returns nothing. Narrations, messages, search queries: never. And critically, `pond schema` calls
> the *same function* that builds the API payload, so the documentation can't drift from the
> implementation.
>
> The second is that model output is untrusted input. My first version of "is this SQL safe" was a
> regex blocklist for DROP and DELETE — which is security theatre, because it loses to a semicolon or
> a CTE. The real version parses the SQL with sqlglot and walks the AST: exactly one statement, must
> be a SELECT, reject any DDL or DML node anywhere in the tree, reject any function that reaches the
> filesystem like `read_csv` or `glob`. And it re-serialises from the validated tree, so what
> executes is exactly what was validated.

**4. The result and what it taught me (20s)**

> It works, 56 tests, CI green, and it answered my original question — which turned out to be a real
> correlation I'd have preferred not to know about. The biggest lesson was about schema design:
> I added calendar-spine views that manufacture a row per day and per week, and the model's
> first-attempt SQL got noticeably better. Giving it a good schema was worth more than giving it a
> better prompt.

---

## STAR stories

### STAR 1 — The SQL guardrails (use this for "tell me about a security decision")

**Situation.** POND's core feature is an LLM writing SQL that then runs against a database
containing my bank transactions and private messages.

**Task.** Make it structurally impossible for generated SQL to modify data or reach the filesystem —
without crippling legitimate analytical queries, which genuinely need CTEs, window functions and
subqueries.

**Action.** My first pass was a regex blocklist. I threw it away once I convinced myself it dies to
a trailing statement after a semicolon, a keyword inside a comment, or a nested CTE. I replaced it
with sqlglot AST validation: parse in the DuckDB dialect, assert exactly one statement, assert the
root is a `Query` node, then walk every node in the tree rejecting `Insert`, `Update`, `Delete`,
`Create`, `Drop`, `Alter`, `Attach`, `Copy`, `Pragma`, `Set` and sqlglot's catch-all `Command` node —
that last one matters, because it's where any DuckDB verb sqlglot doesn't model natively would hide.
Then a function-name check for `read_csv`, `read_parquet`, `glob`, `getenv` and friends. Un-limited
selects get a `LIMIT 500` appended, and the SQL that executes is re-serialised from the validated
tree rather than the original string.

**Result.** Read-only enforcement that holds against multi-statement injection, comment tricks and
nested constructs, with dedicated tests for each. The wider lesson I took: prompt instructions are
not a security control, and validating the artifact beats trusting the producer.

### STAR 2 — Idempotent imports (use this for "tell me about a bug you found the hard way")

**Situation.** Users — me — re-import overlapping exports constantly, because you can't tell from
the filename what date range is inside.

**Task.** Make re-running any import a safe no-op.

**Action.** I built a sha256 file ledger first, felt clever, and then imported Spotify's "account
data" and "extended streaming history" exports back to back. Every play from last March appeared
twice. Two different files, so two different hashes, so the ledger did exactly nothing. The fix was
a second, independent layer: a deterministic `dedupe_key` per row, UNIQUE-constrained, computed as a
sha256 over the natural key. The insert also collapses duplicates *within* a batch using a
`row_number()` window, because a single file can repeat rows too. One subtlety I only caught by
thinking about it: the Spotify key normalises the timestamp to a UTC wall-clock string, because if
the key depended on the session timezone, changing your config would silently re-import your whole
history.

**Result.** Imports are idempotent by construction. `--force` re-reads a file, and row-level dedupe
still refuses to duplicate anything. The lesson: "have I seen this file?" and "have I seen this
row?" are different questions and need different answers.

### STAR 3 — The bank importer (use this for "tell me about a time you changed approach")

**Situation.** Bank CSV exports are the primary data source for the spending half of the product,
and every Indian bank formats them differently — header row position, date format, one signed amount
column versus separate debit/credit, `Cr`/`Dr` suffixes that carry the sign, ₹ symbols, thousands
separators.

**Task.** Import any bank's statement correctly. "Correctly" is load-bearing: a wrong guess doesn't
crash, it silently produces wrong financial data.

**Action.** I spent about three weeks writing detection heuristics. Each round worked for the bank
I was testing and broke a different one. Eventually I accepted that the information genuinely isn't
in the file and stopped trying to infer it. The shipped design asks the user four questions the
first time it meets an unknown format, then saves a `BankProfile` keyed on the **sorted, lowercased
header signature** — not the filename or the bank name, so it still matches when the bank shuffles
its preamble rows around, and the header row index is re-derived per file. In non-interactive
contexts it fails loudly with instructions instead of guessing.

**Result.** Fully automatic on every import after the first, per bank, and no silent corruption. What
I actually learned is that I'd been optimising for a demo — "look, zero configuration!" — rather than
for the user, and the user was me. Sometimes the right amount of automation is "not yet."

---

## Likely technical Q&A

**Q: Why DuckDB and not SQLite or Postgres?**
Postgres means running a server for a single-user local tool — wrong shape. SQLite is the real
alternative, and it loses on the analytics: I lean on window functions, `generate_series` for the
calendar spine, `TIMESTAMPTZ`, `read_json` with an explicit column schema, and an Excel reader — all
in-process. In SQLite the spine views alone would have been meaningful Python. DuckDB is columnar
and built for exactly this aggregate-over-a-few-hundred-thousand-rows workload.

**Q: What actually gets sent to the LLM? Convince me.**
A hand-written schema DDL description with column comments, plus — only when
`privacy.vocab_sharing` is `categorical` — the distinct values of exactly three whitelisted columns,
capped at 50 each: `transactions.category`, `transactions.account`, `activities.activity_type`. All
three are closed, low-cardinality sets. Set it to `none` and you get schema only. And you don't have
to take my word for it: `pond schema` calls the same `build_schema_doc()` the API path calls and
prints the result verbatim.

**Q: Why is the schema doc hand-written rather than introspected?**
Deliberate trade-off, and it's the thing I'd change first. Introspection would auto-track schema
changes, which is a real advantage. But it would also happily describe a column I didn't mean to
expose, and it can't carry the prose conventions — "spending is `SUM(-amount)` over `amount < 0`
rows", "filter `ms_played >= 30000` for real listens" — which do most of the work in getting correct
SQL first try. Today a `db.py` change needs a matching `schema_doc.py` edit and only a human catches
it. The fix is generating it from the DDL with an explicit allowlist.

**Q: What if the model writes SQL that's valid but wrong?**
That's the honest limitation. Guardrails prove it's *safe*, not that it's *correct*. Mitigations:
`--explain` prints the SQL so you can check it, `pond sql` lets you bypass the model entirely, and
the schema doc encodes the conventions that cause the most common semantic errors. Execution errors
feed back for up to two repair attempts — but a query that runs and means the wrong thing will
return the wrong number, and I'd rather say that than pretend otherwise.

**Q: How do you test something with an LLM in it?**
The client is created in a one-line factory, `engine._make_client()`, which exists purely as a
monkeypatch seam. Tests stub it and drive the whole pipeline — prompt, extraction, validation,
execution, retry-on-error — with no API key and no network. Guardrails get their own direct tests
per attack shape. Everything else runs against synthetic fixtures.

**Q: Why is `pond sql` not sandboxed when `pond ask` is?**
Because the asymmetry is the point. `pond sql` is a human typing SQL at their own database on their
own machine — sandboxing that would be theatre, and they could just open the DuckDB file directly
anyway. `pond ask` is a remote model producing SQL. Trust the human, distrust the model.

**Q: What happens with a 500,000-row import?**
Fine, and it's the case DuckDB is built for — the load is `INSERT ... SELECT` inside the database,
not row-by-row in Python, and dedupe is a window function rather than a lookup loop. The weak spot
is `pond categorize`, which rewrites every row unconditionally. Fine at 5k, wasteful at 500k. It
should be incremental.

**Q: How do you know the timezone handling is right?**
Everything is stored as `TIMESTAMPTZ` and the session timezone is set from config on every connect,
so values render in local time. Dedupe keys deliberately do *not* depend on it — they normalise to a
UTC wall-clock string — because otherwise changing your timezone would silently duplicate your entire
history. And manual `pond log` entries land at 12:00 local specifically so they bucket into the
right local day and week regardless of the arithmetic.

**Q: Why a CLI and not a web UI?**
Scope, and honesty about what the tool is. It's a single-user local analytical tool; a web UI would
mean a server, a session model, and a much bigger attack surface for zero added capability. The
output is a table — the terminal is a fine place for a table. `--format csv|json` covers everything
downstream.

---

## What I'd improve next

Say these before you're asked; volunteering them reads much better than conceding them.

1. **Generate the schema doc from the DDL with an explicit allowlist.** Removes the one place where
   the privacy payload can silently drift from the real schema. This is the top of the list.
2. **Make `pond categorize` incremental** — re-categorise only rows whose merchant or rules changed.
3. **Narrow `stage_raw`'s exception handling.** Swallowing every `duckdb.Error` is right for schema
   drift and too broad for a genuine bug; it should surface a warning into `ImportStats`.
4. **Move `weeks.went_to_gym`'s activity synonyms into config.** Right now the view has a personal
   opinion baked into it about what counts as a gym session.
5. **A local model option.** The one network call is the only thing standing between POND and fully
   offline. A small local NL-to-SQL model would close it — at some cost in query quality, which is
   exactly the trade-off worth measuring.
6. **Golden-query regression tests.** A fixed set of questions with expected SQL shapes, so a model
   or prompt change that degrades generation quality shows up as a failing test instead of a vibe.

---

## Smart questions to ask them

- How do you draw the line on what user data is allowed to reach a third-party model — is that a
  written policy, or does it live in individual engineers' judgement?
- When an LLM feature produces plausible-but-wrong output, how do you catch it before a user does?
  Evals, human review, something else?
- What does "done" look like for a feature here — tests and CI, or is there a review or design bar
  beyond that?
- Where's the biggest gap right now between what the system does and what you tell users it does?
- What's something the team believed six months ago that turned out to be wrong?
- If I joined, what would the first thing I own look like — and who would I be pairing with early on?

---

## Things to have open in a tab

- `src/pond/ask/guardrails.py` — the whole security argument, ~90 lines.
- `src/pond/ask/schema_doc.py` — the whole privacy argument.
- `src/pond/db.py` `SPINE_VIEWS` — the "absence is data" idea in ~15 lines of SQL.
- `src/pond/importers/base.py` `insert_dedupe()` — the two-layer idempotency.
- [`PROJECT_DEEP_DIVE.md`](PROJECT_DEEP_DIVE.md) — for anything they push harder on.
