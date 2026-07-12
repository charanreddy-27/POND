"""Bank importer: profile matching, normalization, merchant extraction, categories."""

from __future__ import annotations

import pytest

from pond.categorize import recategorize_all
from pond.importers.bank import BankImporter, BankProfile, find_profile, save_profile

HDFC_PROFILE = BankProfile(
    name="hdfc_savings",
    header_signature=sorted(
        ["date", "narration", "withdrawal amt.", "deposit amt.", "closing balance"]
    ),
    header_row=2,
    date_col="Date",
    narration_col="Narration",
    amount_style="debit_credit",
    debit_col="Withdrawal Amt.",
    credit_col="Deposit Amt.",
    date_format="%d/%m/%y",
)


@pytest.fixture()
def hdfc(pond_env):
    save_profile(HDFC_PROFILE)
    return HDFC_PROFILE


def test_profile_found_with_rederived_header_row(hdfc, fixtures):
    matched = find_profile(fixtures / "bank" / "hdfc_savings.csv")
    assert matched is not None
    profile, header_row = matched
    assert profile.name == "hdfc_savings" and header_row == 2


def test_import_statement(hdfc, con, cfg, fixtures):
    stats = BankImporter().run(
        fixtures / "bank" / "hdfc_savings.csv", con, cfg, account="hdfc_savings"
    )
    # 7 data rows minus the Opening Balance row.
    assert stats.rows_inserted == 6

    rows = con.execute(
        "SELECT amount, merchant, category FROM transactions ORDER BY ts"
    ).fetchall()
    amounts = [float(r[0]) for r in rows]
    assert amounts == [-1240.0, -450.0, -2100.0, 85000.0, -3000.0, -5000.0]

    by_merchant = {r[1]: r[2] for r in rows}
    assert by_merchant["SWIGGY"] == "food_delivery"
    assert by_merchant["ZOMATO"] == "food_delivery"
    assert "AMAZON PAY INDIA" in by_merchant
    assert by_merchant["AMAZON PAY INDIA"] == "shopping"
    assert by_merchant["CULT FIT"] == "fitness"

    # ATM row: no merchant pattern matches, category comes from narration.
    atm = con.execute(
        "SELECT merchant, category FROM transactions WHERE narration LIKE 'ATM%'"
    ).fetchone()
    assert atm[0] is None and atm[1] == "cash"


def test_reimport_is_noop(hdfc, con, cfg, fixtures):
    imp = BankImporter()
    imp.run(fixtures / "bank" / "hdfc_savings.csv", con, cfg, account="hdfc_savings")
    again = imp.run(fixtures / "bank" / "hdfc_savings.csv", con, cfg, account="hdfc_savings")
    assert again.rows_inserted == 0 and again.files_skipped == 1


def test_recategorize_after_rule_edit(hdfc, con, cfg, fixtures, pond_env):
    BankImporter().run(fixtures / "bank" / "hdfc_savings.csv", con, cfg, account="hdfc_savings")
    rules = pond_env / "category_rules.yaml"
    rules.write_text(
        "rules:\n  - match: 'acme'\n    category: salary\n"
        "  - match: 'swiggy'\n    category: food_delivery\n"
    )
    recategorize_all(con)
    salary = con.execute(
        "SELECT category FROM transactions WHERE amount > 0"
    ).fetchone()[0]
    assert salary == "salary"
    zomato = con.execute(
        "SELECT category FROM transactions WHERE merchant = 'ZOMATO'"
    ).fetchone()[0]
    assert zomato is None  # zomato rule removed -> uncategorized again
