"""Bank statement importer (§5.4): CSV/XLSX netbanking exports.

Profiles in ~/.pond/bank_profiles.yaml remember, per bank, where the header
row is, which columns mean what, and how amounts are written — so the
interactive mapping happens once per bank, ever.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import duckdb
import typer
import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from pond.categorize import category_sql_expr, load_rules, merchant_sql_expr
from pond.config import Config, pond_home
from pond.importers.base import BaseImporter, ImportStats, insert_dedupe, register, stage_raw
from pond.ledger import already_imported, file_sha256, record_import

TXN_COLS = [
    "ts",
    "amount",
    "currency",
    "narration",
    "merchant",
    "category",
    "account",
    "source",
    "dedupe_key",
]

# try_strptime cascade used when a profile has no explicit date format.
FALLBACK_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %B %Y",
]

# Strip currency symbols, commas, spaces; keep digits, sign, dot, Cr/Dr flag.
_CLEAN = "regexp_replace({col}, '[^0-9.CDcrRd-]', '', 'g')"


class BankProfile(BaseModel):
    """A learned column mapping for one bank's export format."""

    name: str
    header_signature: list[str]  # sorted lowercase header names
    header_row: int = 0
    date_col: str
    narration_col: str
    amount_style: str = "signed"  # 'signed' | 'debit_credit'
    amount_col: str | None = None
    debit_col: str | None = None
    credit_col: str | None = None
    date_format: str | None = None


def profiles_path() -> Path:
    """Location of the learned profiles file."""
    return pond_home() / "bank_profiles.yaml"


def load_profiles() -> list[BankProfile]:
    """All saved profiles (empty list if none yet)."""
    path = profiles_path()
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [BankProfile.model_validate(p) for p in data.get("profiles", [])]


def save_profile(profile: BankProfile) -> None:
    """Append/replace a profile by name."""
    profiles = [p for p in load_profiles() if p.name != profile.name]
    profiles.append(profile)
    profiles_path().parent.mkdir(parents=True, exist_ok=True)
    with open(profiles_path(), "w") as f:
        yaml.safe_dump({"profiles": [p.model_dump() for p in profiles]}, f, sort_keys=False)


def _signature(headers: list[str]) -> list[str]:
    return sorted(h.strip().lower() for h in headers if h.strip())


def _read_rows(f: Path, limit: int = 30) -> list[list[str]]:
    """First ``limit`` raw rows of a CSV for header hunting / previews."""
    with open(f, newline="", encoding="utf-8", errors="replace") as fh:
        return [row for _, row in zip(range(limit), csv.reader(fh), strict=False)]


def find_profile(f: Path) -> tuple[BankProfile, int] | None:
    """Match a saved profile by header signature anywhere in the first rows.

    Returns (profile, header_row_index) — the row index is re-derived per file
    since banks love shifting their preamble around.
    """
    rows = _read_rows(f)
    for i, row in enumerate(rows):
        sig = _signature(row)
        if not sig:
            continue
        for p in load_profiles():
            if sig == p.header_signature:
                return p, i
    return None


@register
class BankImporter(BaseImporter):
    """CSV/XLSX statements. Lowest-priority detect; PDF is out of scope."""

    id = "bank"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file():
            return path.suffix.lower() in {".csv", ".xls", ".xlsx"}
        return any(path.glob("*.csv")) or any(path.glob("*.xlsx"))

    def run(
        self,
        path: Path,
        con: duckdb.DuckDBPyConnection,
        cfg: Config,
        force: bool = False,
        account: str | None = None,
        **kwargs: object,
    ) -> ImportStats:
        stats = ImportStats(tables={"transactions"})
        files = (
            [path]
            if path.is_file()
            else sorted(list(path.glob("*.csv")) + list(path.glob("*.xlsx")))
        )
        for f in files:
            sha = file_sha256(f)
            if not force and already_imported(con, sha):
                stats.files_skipped += 1
                continue
            if f.suffix.lower() in {".xls", ".xlsx"}:
                csv_f = _xlsx_to_csv(con, f, stats)
                if csv_f is None:
                    continue
            else:
                csv_f = f
            matched = find_profile(csv_f)
            if matched is None:
                profile, header_row = interactive_profile(csv_f)
            else:
                profile, header_row = matched
            ins, skip = _load_statement(
                con,
                cfg,
                csv_f,
                profile,
                header_row,
                account or profile.name,
            )
            stats.rows_inserted += ins
            stats.rows_skipped += skip
            record_import(con, sha, f, self.id, ins)
        return stats


def _xlsx_to_csv(con: duckdb.DuckDBPyConnection, f: Path, stats: ImportStats) -> Path | None:
    """Convert an Excel statement to CSV via DuckDB's excel reader (best effort)."""
    out = f.with_suffix(".pond-tmp.csv")
    try:
        con.execute(
            "COPY (SELECT * FROM read_xlsx(?, all_varchar = true)) TO ? (HEADER, DELIMITER ',')",
            [str(f), str(out)],
        )
        return out
    except duckdb.Error as e:
        stats.warnings.append(f"{f.name}: could not read Excel ({e}); re-export as CSV and retry")
        return None


def interactive_profile(f: Path) -> tuple[BankProfile, int]:
    """First-time column mapping for an unknown bank format (§5.4 step 2)."""
    if not sys.stdin.isatty():
        raise typer.BadParameter(
            f"No saved bank profile matches {f.name}. "
            "Run `pond import --source bank` in an interactive terminal once "
            "to teach POND this bank's format."
        )
    console = Console()
    rows = _read_rows(f, limit=10)
    table = Table(title=f"First rows of {f.name}", show_lines=False)
    table.add_column("row#")
    width = max(len(r) for r in rows) if rows else 0
    for i in range(width):
        table.add_column(f"col {i}")
    for i, row in enumerate(rows[:6]):
        table.add_row(str(i), *(row + [""] * (width - len(row))))
    console.print(table)

    header_row = typer.prompt("Which row number holds the column headers?", type=int, default=0)
    headers = rows[header_row]
    console.print(f"Headers: {headers}")
    date_col = typer.prompt("Which column is the transaction *date*? (name)")
    narration_col = typer.prompt("Which column is the *narration/description*? (name)")
    single = typer.confirm("Is the amount a single signed column? (No = separate debit/credit)")
    amount_col = debit_col = credit_col = None
    if single:
        amount_col = typer.prompt("Amount column name")
        style = "signed"
    else:
        debit_col = typer.prompt("Debit (withdrawal) column name")
        credit_col = typer.prompt("Credit (deposit) column name")
        style = "debit_credit"
    date_format = (
        typer.prompt(
            "Date format (strptime, e.g. %d/%m/%Y) — empty to auto-detect",
            default="",
            show_default=False,
        )
        or None
    )
    name = typer.prompt("Name this profile (e.g. hdfc_savings)")
    profile = BankProfile(
        name=name,
        header_signature=_signature(headers),
        header_row=header_row,
        date_col=date_col,
        narration_col=narration_col,
        amount_style=style,
        amount_col=amount_col,
        debit_col=debit_col,
        credit_col=credit_col,
        date_format=date_format,
    )
    save_profile(profile)
    console.print(f"[green]Saved profile '{name}' — future imports are automatic.[/green]")
    return profile, header_row


def _amount_expr(profile: BankProfile) -> str:
    """SQL producing a signed DECIMAL amount: negative = money out (§3.1)."""

    def clean_num(col: str) -> str:
        # Strip ₹/commas/spaces, honour Cr/Dr suffixes, then cast.
        raw = f'"{col}"'
        num = f"TRY_CAST(regexp_replace({raw}, '[^0-9.-]', '', 'g') AS DECIMAL(12,2))"
        return (
            f"CASE WHEN regexp_matches(upper(coalesce({raw}, '')), 'DR\\.?\\s*$') THEN -abs({num}) "
            f"     WHEN regexp_matches(upper(coalesce({raw}, '')), 'CR\\.?\\s*$') THEN abs({num}) "
            f"     ELSE {num} END"
        )

    if profile.amount_style == "debit_credit":
        debit, credit = clean_num(profile.debit_col or ""), clean_num(profile.credit_col or "")
        return f"coalesce(-abs({debit}), abs({credit}))"
    return clean_num(profile.amount_col or "")


def _date_expr(profile: BankProfile) -> str:
    """SQL parsing the date column, honouring the profile format first."""
    col = f'trim("{profile.date_col}")'
    attempts = []
    if profile.date_format:
        attempts.append(f"try_strptime({col}, '{profile.date_format}')")
    attempts += [f"try_strptime({col}, '{fmt}')" for fmt in FALLBACK_DATE_FORMATS]
    return "coalesce(" + ", ".join(attempts) + ")"


def _load_statement(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    f: Path,
    profile: BankProfile,
    header_row: int,
    account: str,
) -> tuple[int, int]:
    """Normalize one statement file into `transactions` (§5.4 steps 3-5)."""
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _stg_bank_raw AS "
        "SELECT * FROM read_csv_auto(?, skip = ?, header = true, all_varchar = true)",
        [str(f), header_row],
    )
    stage_raw(con, "raw_bank", "SELECT ? AS file, * FROM _stg_bank_raw", [f.name])
    tz = cfg.me.timezone.replace("'", "''")
    date_e, amount_e = _date_expr(profile), _amount_expr(profile)
    narr = f'trim("{profile.narration_col}")'
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _stg_bank AS
        SELECT ({date_e}) AT TIME ZONE '{tz}' AS ts,
               {amount_e} AS amount,
               '{cfg.me.currency}' AS currency,
               {narr} AS narration
        FROM _stg_bank_raw
        WHERE ({date_e}) IS NOT NULL AND ({amount_e}) IS NOT NULL
          AND coalesce({narr}, '') <> ''
          AND NOT regexp_matches(lower({narr}), '(opening|closing) balance')
        """
    )
    case, params = category_sql_expr(load_rules())
    acct = account.replace("'", "''")
    select = f"""
        SELECT ts, amount, currency, narration, merchant, {case} AS category,
               '{acct}' AS account, 'bank' AS source,
               sha256(concat_ws('|', 'bank', '{acct}',
                      strftime(ts AT TIME ZONE '{tz}', '%Y-%m-%d'),
                      amount::VARCHAR, coalesce(narration, ''))) AS dedupe_key
        FROM (SELECT *, {merchant_sql_expr()} AS merchant FROM _stg_bank)
    """
    return insert_dedupe(con, "transactions", TXN_COLS, select, params)
