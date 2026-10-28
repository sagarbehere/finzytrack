"""The mirror's rows, one behaviour per test, each from the spec text (design/specs/mirror/README.md and
surface.toml; ADR-0023, ADR-0024, ADR-0025 amendment, ADR-0026, ADR-0031, ADR-0048, ADR-0055) with the near miss
beside it (EP-29). Ledgers are synthetic (INV-23) and built in temp folders (INV-24); the rows are read through the
public and compatibility views, as users' SQL reads them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from beancount.loader import LoadError

from ledger_engine.mirror.core import schema
from ledger_engine.mirror.core.rows import error_rows

from .support import STAMP, build_text, one, rows

pytestmark = pytest.mark.inv("INV-06")

OPEN = """option "operating_currency" "USD"
2020-01-01 open Assets:Bank
2020-01-01 open Assets:Card
2020-01-01 open Assets:Broker
2020-01-01 open Liabilities:Card
2020-01-01 open Expenses:Food
2020-01-01 open Expenses:Fees
2020-01-01 open Income:Salary
2020-01-01 open Equity:Opening
"""


# ---- postings ------------------------------------------------------------------------------------------------------


def test_a_two_leg_transaction_gives_two_posting_rows(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-05-10 * "Cafe" "lunch" #b #a ^z ^y\n  Expenses:Food  10.50 USD\n  Assets:Bank\n',
    )
    got = rows(
        con,
        "SELECT posting_id, transaction_date, transaction_flag, transaction_payee, transaction_narration,"
        " transaction_tags, transaction_links, account, account_type, account_kind, amount, currency,"
        " year, month, quarter, year_month FROM r_postings ORDER BY posting_id",
    )
    common = ("2021-05-10", "*", "Cafe", "lunch", '["a", "b"]', '["y", "z"]')
    assert got == [
        (1, *common, "Expenses:Food", "Expenses", "Expenses", "10.50", "USD", 2021, 5, 2, "2021-05"),
        (2, *common, "Assets:Bank", "Assets", "Assets", "-10.50", "USD", 2021, 5, 2, "2021-05"),
    ]


def test_no_payee_is_null_and_no_tags_are_an_empty_array(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-05-11 * "only narration"\n  Expenses:Food  1 USD\n  Assets:Bank\n'
        "2021-05-12 *\n  Expenses:Food  1 USD\n  Assets:Bank\n",
    )
    got = rows(
        con,
        "SELECT DISTINCT transaction_payee, transaction_narration, transaction_tags, transaction_links"
        " FROM postings ORDER BY transaction_date",
    )
    assert got == [(None, "only narration", "[]", "[]"), (None, "", "[]", "[]")]


def test_numbers_are_plain_notation_with_the_written_scale(tmp_path: Path) -> None:
    # README "Exact numbers": format(d, "f"), never `1E-8`; the elided leg keeps the scale Beancount computed.
    con = build_text(
        tmp_path,
        OPEN + '2021-01-01 * "btc"\n  Assets:Broker  0.00000001 BTC\n  Equity:Opening\n'
        '2021-01-02 * "round"\n  Assets:Bank  100 USD\n  Equity:Opening  -100.00 USD\n',
    )
    got = rows(con, "SELECT amount FROM postings ORDER BY posting_id")
    assert got == [("0.00000001",), ("-0.00000001",), ("100",), ("-100.00",)]


def test_cost_and_price_columns_come_from_booking(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-02-01 * "buy"\n  Assets:Broker  10 VTI {100 USD, "lot-a"}\n  Assets:Bank\n'
        '2021-03-01 * "sell"\n  Assets:Broker  -4 VTI {} @ 120 USD\n  Assets:Bank  480 USD\n'
        "  Income:Salary\n",
    )
    got = rows(
        con,
        "SELECT amount, cost_amount, cost_currency, cost_date, cost_label, price_amount, price_currency"
        " FROM r_postings WHERE account = 'Assets:Broker' ORDER BY posting_id",
    )
    # The reduction gets the matched lot's cost, date and label (booking), and its own price.
    assert got == [
        ("10", "100", "USD", "2021-02-01", "lot-a", None, None),
        ("-4", "100", "USD", "2021-02-01", "lot-a", "120", "USD"),
    ]


def test_padding_transactions_are_postings_with_flag_p(tmp_path: Path) -> None:
    con = build_text(
        tmp_path, OPEN + "2020-01-04 pad Assets:Bank Equity:Opening\n2020-01-05 balance Assets:Bank 50 USD\n"
    )
    got = rows(con, "SELECT transaction_flag, transaction_date, account, amount FROM postings ORDER BY posting_id")
    assert got == [("P", "2020-01-04", "Assets:Bank", "50"), ("P", "2020-01-04", "Equity:Opening", "-50")]


def test_custom_root_names_keep_the_written_root_and_map_the_kind(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        'option "name_assets" "Activos"\noption "name_expenses" "Gastos"\n'
        "2020-01-01 open Activos:Banco\n2020-01-01 open Gastos:Comida\n"
        '2021-01-01 * "Mercado" "compra"\n  Gastos:Comida  5 EUR\n  Activos:Banco\n',
    )
    got = rows(con, "SELECT account, account_type, account_kind FROM r_postings ORDER BY posting_id")
    assert got == [("Gastos:Comida", "Gastos", "Expenses"), ("Activos:Banco", "Activos", "Assets")]
    assert rows(con, "SELECT name, account_kind FROM r_accounts ORDER BY name") == [
        ("Activos:Banco", "Assets"),
        ("Gastos:Comida", "Expenses"),
    ]
    # training_data finds the category by kind, not by the literal `Expenses:` prefix (surface.toml `category`).
    assert rows(con, "SELECT description, category FROM training_data") == [("Mercado compra", "Gastos:Comida")]


def test_document_count_counts_meta_keys_starting_with_document(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-01-01 * "x"\n  document: "a.pdf"\n  document2: "b.pdf"\n  documented: "no"\n'
        '  doc: "c.pdf"\n  Expenses:Food  1 USD\n  Assets:Bank\n'
        '2021-01-02 * "y"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    )
    # `documented` starts with "document" too (v0.2.2's rule, kept); `doc` does not.
    assert rows(con, "SELECT DISTINCT transaction_date, document_count FROM postings ORDER BY 1") == [
        ("2021-01-01", 3),
        ("2021-01-02", 0),
    ]


def source_accounts(tmp_path: Path, body: str) -> list[tuple[str, str | None, str | None]]:
    con = build_text(tmp_path, OPEN + body)
    return [
        (r[0], r[1], r[2])
        for r in rows(con, "SELECT account, source_account, source_account_type FROM postings ORDER BY posting_id")
    ]


def test_source_account_rule_1_the_legacy_key_on_every_row(tmp_path: Path) -> None:
    body = (
        '2021-01-01 * "x"\n  source_account: "Assets:Card"\n'
        "  Expenses:Food  6 USD\n  Expenses:Fees  1 USD\n  Assets:Bank  -7 USD\n"
    )
    assert source_accounts(tmp_path, body) == [
        ("Expenses:Food", "Assets:Card", "Assets"),
        ("Expenses:Fees", "Assets:Card", "Assets"),
        ("Assets:Bank", "Assets:Card", "Assets"),
    ]


def test_source_account_rule_1_keeps_an_empty_value(tmp_path: Path) -> None:
    body = '2021-01-01 * "x"\n  source_account: ""\n  Expenses:Food  6 USD\n  Assets:Bank\n'
    assert source_accounts(tmp_path, body) == [("Expenses:Food", "", ""), ("Assets:Bank", "", "")]


def test_source_account_rule_2_the_leg_with_an_import_key(tmp_path: Path) -> None:
    body = (
        '2021-01-01 * "x"\n  Expenses:Food  6 USD\n  Expenses:Fees  1 USD\n'
        '  Liabilities:Card  -7 USD\n    import-key: "v1:card:0001"\n'
    )
    assert source_accounts(tmp_path, body) == [
        ("Expenses:Food", "Liabilities:Card", "Liabilities"),
        ("Expenses:Fees", "Liabilities:Card", "Liabilities"),
        ("Liabilities:Card", "Liabilities:Card", "Liabilities"),
    ]


def test_source_account_rule_2_comes_before_the_other_leg_of_two(tmp_path: Path) -> None:
    # Every v0.4 import's shape (S0050 verifier F3): two legs, the key on the bank leg. Rule 3 would give the bank
    # row Expenses:Food.
    body = '2021-01-01 * "Cafe"\n  Expenses:Food  6 USD\n  Assets:Bank  -6 USD\n    import-key: "v1:bank:0001"\n'
    assert source_accounts(tmp_path, body) == [
        ("Expenses:Food", "Assets:Bank", "Assets"),
        ("Assets:Bank", "Assets:Bank", "Assets"),
    ]


def test_source_account_rule_3_the_other_leg_of_two(tmp_path: Path) -> None:
    body = '2021-01-01 * "x"\n  Expenses:Food  6 USD\n  Income:Salary\n'
    assert source_accounts(tmp_path, body) == [
        ("Expenses:Food", "Income:Salary", "Income"),
        ("Income:Salary", "Expenses:Food", "Expenses"),
    ]


def test_source_account_rule_4_the_first_other_balance_sheet_leg_else_null(tmp_path: Path) -> None:
    body = (
        '2021-01-01 * "x"\n  Expenses:Food  6 USD\n  Expenses:Fees  1 USD\n'
        "  Liabilities:Card  -3 USD\n  Assets:Bank  -4 USD\n"
    )
    assert source_accounts(tmp_path, body) == [
        ("Expenses:Food", "Liabilities:Card", "Liabilities"),
        ("Expenses:Fees", "Liabilities:Card", "Liabilities"),
        ("Liabilities:Card", "Assets:Bank", "Assets"),
        ("Assets:Bank", "Liabilities:Card", "Liabilities"),
    ]
    body = '2021-01-01 * "x"\n  Expenses:Food  6 USD\n  Expenses:Fees  1 USD\n  Income:Salary  -7 USD\n'
    assert source_accounts(tmp_path / "2", body) == [
        ("Expenses:Food", None, None),
        ("Expenses:Fees", None, None),
        ("Income:Salary", None, None),
    ]


def test_source_account_rule_4_matches_the_literal_prefixes(tmp_path: Path) -> None:
    # v0.2.2's literal `Assets:`/`Liabilities:`: a custom asset root is not found.
    con = build_text(
        tmp_path,
        'option "name_assets" "Activos"\n2020-01-01 open Activos:Banco\n2020-01-01 open Expenses:A\n'
        "2020-01-01 open Expenses:B\n"
        '2021-01-01 * "x"\n  Expenses:A  1 EUR\n  Expenses:B  1 EUR\n  Activos:Banco  -2 EUR\n',
    )
    assert rows(con, "SELECT DISTINCT source_account FROM postings") == [(None,)]


# ---- accounts ------------------------------------------------------------------------------------------------------


def test_accounts_keep_the_earliest_open_and_the_latest_close(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        '2020-01-05 open Assets:Bank USD,EUR "FIFO"\n  note: "second"\n'
        '2020-01-01 open Assets:Bank\n  note: "first"\n'
        "2020-01-01 open Equity:Opening\n"
        "2020-03-01 close Assets:Bank\n2020-03-02 close Assets:Bank\n",
    )
    got = rows(con, "SELECT name, open_date, close_date, currencies_json, booking FROM accounts ORDER BY name")
    assert got == [
        ("Assets:Bank", "2020-01-01", "2020-03-02", "[]", None),
        ("Equity:Opening", "2020-01-01", None, "[]", None),
    ]
    meta = json.loads(one(con, "SELECT metadata_json FROM accounts WHERE name = 'Assets:Bank'")[0])
    assert meta["note"] == "first"


def test_accounts_keep_the_first_open_in_entry_order_on_a_date_tie(tmp_path: Path) -> None:
    # S0050 verifier F5: two opens on one date; the one Beancount orders first (the file order) is kept.
    con = build_text(
        tmp_path,
        '2020-01-01 open Assets:Bank\n  note: "first"\n2020-01-01 open Assets:Bank USD "FIFO"\n  note: "second"\n',
    )
    got = one(con, "SELECT currencies_json, booking, metadata_json FROM accounts")
    assert got[:2] == ("[]", None)
    assert json.loads(got[2])["note"] == "first"


def test_accounts_currencies_in_written_order_and_the_booking_text(tmp_path: Path) -> None:
    con = build_text(tmp_path, '2020-01-01 open Assets:Broker USD,EUR,VTI "FIFO"\n')
    assert one(con, "SELECT currencies_json, booking FROM accounts") == ('["USD", "EUR", "VTI"]', "FIFO")


# ---- balance assertions and pad pairing ------------------------------------------------------------------------------


def test_pad_pairing_the_readme_example(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Bank:Usd\n2020-01-01 open Equity:Opening\n"
        "2020-01-04 pad Assets:Bank:Usd Equity:Opening\n2020-01-05 balance Assets:Bank:Usd 50 USD\n",
    )
    got = one(
        con,
        "SELECT id, date, amount_number, passed, diff_number, pad_date, pad_source_account FROM r_balance_assertions",
    )
    assert got == (1, "2020-01-05", "50", 1, None, "2020-01-04", "Equity:Opening")


def test_each_assertion_is_paired_with_the_pad_that_padded_it(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + "2020-01-01 open Equity:Other\n"
        "2020-01-04 pad Assets:Bank Equity:Opening\n2020-01-05 balance Assets:Bank 50 USD\n"
        "2020-02-01 pad Assets:Card Equity:Other\n2020-02-02 balance Assets:Card 20 USD\n",
    )
    assert rows(con, "SELECT account, pad_date, pad_source_account FROM balance_assertions ORDER BY id") == [
        ("Assets:Bank", "2020-01-04", "Equity:Opening"),
        ("Assets:Card", "2020-02-01", "Equity:Other"),
    ]


def test_an_assertion_that_already_matches_gets_no_padding(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2020-01-02 * "in"\n  Assets:Bank  50 USD\n  Income:Salary\n'
        "2020-01-04 pad Assets:Bank Equity:Opening\n2020-01-05 balance Assets:Bank 50 USD\n",
    )
    assert one(con, "SELECT passed, pad_date, pad_source_account FROM balance_assertions") == (1, None, None)


def test_one_pad_serves_the_first_assertion_of_each_currency(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + "2020-01-04 pad Assets:Bank Equity:Opening\n"
        "2020-01-05 balance Assets:Bank 50 USD\n2020-01-05 balance Assets:Bank 20 EUR\n"
        "2020-01-06 balance Assets:Bank 60 USD\n",
    )
    got = rows(
        con,
        "SELECT date, amount_currency, passed, diff_number, diff_currency, pad_date"
        " FROM balance_assertions ORDER BY id",
    )
    assert got == [
        ("2020-01-05", "USD", 1, None, None, "2020-01-04"),
        ("2020-01-05", "EUR", 1, None, None, "2020-01-04"),
        ("2020-01-06", "USD", 0, "-10", "USD", None),
    ]


def test_a_written_tolerance_is_kept_and_none_is_null(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2020-01-02 * "in"\n  Assets:Bank  50.30 USD\n  Income:Salary\n'
        "2020-01-05 balance Assets:Bank 50.00 ~ 0.50 USD\n2020-01-06 balance Assets:Bank 50.30 USD\n",
    )
    assert rows(con, "SELECT tolerance, passed FROM balance_assertions ORDER BY id") == [("0.50", 1), (None, 1)]


# ---- directive tables -----------------------------------------------------------------------------------------------


def test_the_directive_tables(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + "2020-01-04 pad Assets:Bank Equity:Opening\n"
        '2020-02-01 note Assets:Bank "called the bank" #t2 #t1 ^l1\n'
        '2020-02-02 note Assets:Bank "no tags"\n'
        '2020-02-02 document Assets:Bank "statement.pdf" #t ^l\n'
        '2020-02-03 event "location" "Pune"\n'
        '2020-02-04 query "food" "SELECT account"\n'
        '2020-02-05 custom "budget" Expenses:Food "monthly" 100.00 USD TRUE\n'
        "2020-02-06 price VTI 101.50 USD\n",
        files={"statement.pdf": "synthetic"},
    )
    assert rows(con, "SELECT id, date, account, source_account FROM pad_directives") == [
        (1, "2020-01-04", "Assets:Bank", "Equity:Opening")
    ]
    assert rows(con, "SELECT id, comment, tags_json, links_json FROM notes ORDER BY id") == [
        (1, "called the bank", '["t1", "t2"]', '["l1"]'),
        (2, "no tags", None, None),
    ]
    document = one(con, "SELECT id, account, filename, tags_json, links_json FROM documents")
    assert document == (1, "Assets:Bank", str(tmp_path / "ledger" / "statement.pdf"), '["t"]', '["l"]')
    assert rows(con, "SELECT id, type, description FROM events") == [(1, "location", "Pune")]
    assert rows(con, "SELECT id, name, query_string FROM stored_queries") == [(1, "food", "SELECT account")]
    values = json.loads(one(con, "SELECT values_json FROM custom_directives WHERE type = 'budget'")[0])
    # README "Metadata JSON": an array of [value, type-tag] pairs, read by position.
    assert values == [
        ["Expenses:Food", "<AccountDummy>"],
        ["monthly", "<class 'str'>"],
        [["100.00", "USD"], "<class 'beancount.core.amount.Amount'>"],
        [True, "<class 'bool'>"],
    ]
    assert rows(con, "SELECT id, date, base_currency, quote_number, quote_currency FROM prices") == [
        (1, "2020-02-06", "VTI", "101.50", "USD")
    ]


def test_prices_include_those_a_plugin_makes(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        'plugin "beancount.plugins.implicit_prices"\n'
        + OPEN
        + '2021-02-01 * "buy"\n  Assets:Broker  3 VTI {10.25 USD} @ 11 USD\n  Assets:Bank\n',
    )
    assert rows(con, "SELECT date, base_currency, quote_number, quote_currency FROM prices") == [
        ("2021-02-01", "VTI", "11", "USD")
    ]


def test_metadata_json_conversion(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-01-01 * "x"\n  rate: 10.50\n  when: 2020-12-31\n  limit: 1.25 USD\n  ok: TRUE\n'
        '  city: "Café"\n  Expenses:Food  1 USD\n  Assets:Bank\n',
    )
    text = one(con, "SELECT DISTINCT transaction_metadata_json FROM postings")[0]
    meta = json.loads(text)
    assert {k: meta[k] for k in ("rate", "when", "limit", "ok", "city")} == {
        "rate": "10.50",
        "when": "2020-12-31",
        "limit": ["1.25", "USD"],
        "ok": True,
        "city": "Café",
    }
    assert "\\u00e9" in text  # json.dumps defaults: non-ASCII escaped
    # Beancount's own keys are kept; an interpolated leg carries `__automatic__`.
    assert meta["lineno"] == 10
    assert meta["filename"] == str(tmp_path / "ledger" / "main.beancount")
    posting = json.loads(one(con, "SELECT posting_metadata_json FROM postings WHERE account = 'Assets:Bank'")[0])
    assert posting["__automatic__"] is True


# ---- options and errors -------------------------------------------------------------------------------------------


def test_ledger_options(tmp_path: Path) -> None:
    con = build_text(tmp_path, OPEN)
    options = dict(rows(con, "SELECT key, value_json FROM ledger_options"))
    assert options["operating_currency"] == '["USD"]'
    assert options["booking_method"] == '"Booking.STRICT"'
    assert options["name_assets"] == '"Assets"'
    assert {"filename", "include", "input_hash", "dcontext"} <= set(options)


def test_ledger_errors(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        'include "missing.beancount"\n' + OPEN + "2020-01-05 balance Assets:Bank 50 USD\n",
    )
    got = rows(
        con, "SELECT id, source_file, line_number, message, entry_json, error_type FROM r_ledger_errors ORDER BY id"
    )
    assert [(r[0], r[5]) for r in got] == [(1, "LoadError"), (2, "BalanceError")]
    load_error, balance_error = got
    assert load_error[1:3] == ("<load>", 0)
    assert load_error[4] is None  # an error with no entry
    assert balance_error[1:3] == (str(tmp_path / "ledger" / "main.beancount"), 11)
    assert balance_error[3].startswith("Balance failed for 'Assets:Bank'")
    entry = json.loads(balance_error[4])
    assert entry[0]["lineno"] == 11
    assert entry[1:4] == ["2020-01-05", "Assets:Bank", ["50", "USD"]]


def test_an_error_with_a_file_but_no_line_has_a_null_line() -> None:
    # A plugin's error may name a file and no line; v0.2.2 wrote NULL (surface.toml `line_number`, change "same").
    (row,) = error_rows([LoadError({"filename": "plugins/check.py"}, "plugin failed", None)])
    assert row[1:3] == ("plugins/check.py", None)


# ---- balances, lots and usage -------------------------------------------------------------------------------------


def test_account_balances_stop_at_today(tmp_path: Path) -> None:
    # TODAY is 2026-10-28 (support.py): an entry of that day counts, one of the next day does not.
    con = build_text(
        tmp_path,
        OPEN + '2026-10-27 * "a"\n  Assets:Bank  10.50 USD\n  Income:Salary\n'
        '2026-10-28 * "b"\n  Assets:Bank  0.25 USD\n  Income:Salary\n'
        '2026-10-29 * "future"\n  Assets:Bank  1000 USD\n  Income:Salary\n',
    )
    assert rows(con, "SELECT * FROM account_balances ORDER BY account") == [
        ("Assets:Bank", "USD", "10.75", 2, "2026-10-28"),
        ("Income:Salary", "USD", "-10.75", 2, "2026-10-28"),
    ]


def test_account_balances_omit_zero_nets_and_count_postings(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Bank:Sub\n2020-01-01 open Income:Salary\n"
        '2021-01-01 * "in"\n  Assets:Bank  5 USD\n  Assets:Bank  5 USD\n  Income:Salary\n'
        '2021-01-02 * "sub"\n  Assets:Bank:Sub  3 USD\n  Income:Salary\n'
        '2021-01-03 * "out"\n  Income:Salary  13 USD\n  Assets:Bank  -10 USD\n  Assets:Bank:Sub  -3 USD\n',
    )
    # Every net is zero but the parent's own rows: sub-accounts are separate rows, zero nets have no row.
    assert rows(con, "SELECT * FROM account_balances") == []
    con = build_text(
        tmp_path / "2",
        "2020-01-01 open Assets:Bank\n2020-01-01 open Assets:Bank:Sub\n2020-01-01 open Income:Salary\n"
        '2021-01-01 * "in"\n  Assets:Bank  5 USD\n  Assets:Bank  5 USD\n  Income:Salary\n'
        '2021-01-02 * "sub"\n  Assets:Bank:Sub  3 USD\n  Income:Salary\n',
    )
    assert rows(con, "SELECT account, balance, transaction_count FROM account_balances ORDER BY account") == [
        ("Assets:Bank", "10", 2),
        ("Assets:Bank:Sub", "3", 1),
        ("Income:Salary", "-13", 2),
    ]


def test_account_balances_sum_every_lot(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-02-01 * "a"\n  Assets:Broker  2 VTI {100 USD}\n  Assets:Bank\n'
        '2021-02-02 * "b"\n  Assets:Broker  3 VTI {110 USD}\n  Assets:Bank\n',
    )
    assert one(con, "SELECT balance, transaction_count FROM account_balances WHERE currency = 'VTI'") == ("5", 2)


def test_lots_are_label_aware_and_stop_at_today(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        # Lot "b" is bought first; the rows are still ordered by label (surface.toml `lots.id`).
        OPEN + '2021-02-01 * "b"\n  Assets:Broker  3 VTI {10.25 USD, "b"}\n  Assets:Bank\n'
        '2021-02-01 * "a"\n  Assets:Broker  2 VTI {10.25 USD, "a"}\n  Assets:Bank\n'
        '2021-01-15 * "c"\n  Assets:Broker  1 AAA {7 USD}\n  Assets:Bank\n'
        '2021-03-01 * "sold"\n  Assets:Broker  1 BBB {5 USD}\n  Assets:Bank\n'
        '2021-03-02 * "sold"\n  Assets:Broker  -1 BBB {5 USD}\n  Assets:Bank\n'
        '2026-10-29 * "future"\n  Assets:Broker  9 VTI {11 USD}\n  Assets:Bank\n',
    )
    assert rows(con, "SELECT * FROM lots ORDER BY id") == [
        (1, "Assets:Broker", "1", "AAA", "7", "USD", "2021-01-15", None, "7"),
        (2, "Assets:Broker", "2", "VTI", "10.25", "USD", "2021-02-01", "a", "20.50"),
        (3, "Assets:Broker", "3", "VTI", "10.25", "USD", "2021-02-01", "b", "30.75"),
    ]


def test_commodity_usage_runs_to_the_ledger_end(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-01-01 * "a"\n  Assets:Bank  10.50 USD\n  Income:Salary\n'
        '2026-12-01 * "future"\n  Assets:Bank  -0.50 USD\n  Income:Salary\n',
    )
    assert rows(con, "SELECT * FROM commodity_usage") == [("USD", 4, "22.00", "2021-01-01", "2026-12-01")]


# ---- training data and meta ---------------------------------------------------------------------------------------


def test_training_data(tmp_path: Path) -> None:
    con = build_text(
        tmp_path,
        OPEN + '2021-01-01 * "Cafe" "lunch"\n  memo: "POS 12"\n  Assets:Bank  -5 USD\n  Expenses:Food\n'
        '2021-01-02 * "Grocer" "weekly"\n  ofx_memo: "POS 34"\n  Expenses:Fees  1 USD\n  Expenses:Food  4 USD\n'
        "  Assets:Bank\n"
        '2021-01-03 * "no payee"\n  Expenses:Food  1 USD\n  Assets:Bank\n'
        '2021-01-04 * "Employer" ""\n  Assets:Bank  9 USD\n  Income:Salary\n'
        '2021-01-05 * "Transfer" "card"\n  Assets:Bank  -9 USD\n  Liabilities:Card\n',
    )
    assert rows(con, "SELECT * FROM training_data ORDER BY id") == [
        (1, "Cafe POS 12 lunch", "Expenses:Food"),
        (2, "Grocer POS 34 weekly", "Expenses:Fees"),
        (3, "Employer", "Income:Salary"),
    ]


def test_meta(tmp_path: Path) -> None:
    con = build_text(tmp_path, OPEN)
    assert dict(rows(con, "SELECT key, value FROM meta")) == {
        "surface_version": "1",
        "ledger_version": STAMP.ledger_version,
        "fingerprint": STAMP.fingerprint,
        "export_version": hashlib.sha256(schema.ddl().encode("utf-8")).hexdigest(),
        "built_at": STAMP.built_at,
        "build_complete": "0",  # no `commodities` rows yet (BL-0120)
    }
