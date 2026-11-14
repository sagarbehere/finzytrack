"""Canonical entry keys and error keys (S0116; ADR-0008 steps 2 and 3): what moves with a splice is left out, what
the user wrote is kept."""

from __future__ import annotations

from beancount import loader
from beancount.parser import parser

from ledger_engine.ledger.core import entry_keys
from ledger_engine.mirror.core import rows

TXN = '2024-01-05 * "Shop" "lunch" #t ^l\n  memo: "x"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n'


def key(text: str, path: str = "/l/main.bean") -> str:
    entries, errors, _ = parser.parse_string(text, path)
    assert not errors
    assert len(entries) == 1
    return entry_keys.entry_key(entries[0])


def test_an_entry_s_key_leaves_out_its_line_and_keeps_its_text() -> None:
    assert key(TXN) == key("\n\n; moved down\n" + TXN)
    assert key(TXN) == entry_keys.key_of_text(TXN, "/l/main.bean")
    for changed in (
        TXN.replace("10.50", "10.5"),  # the same number, other decimals
        TXN.replace("*", "!"),
        TXN.replace("#t", "#u"),
        TXN.replace('memo: "x"', 'memo: "y"'),
        TXN.replace("  Assets:Cash\n", "  Assets:Bank\n"),
        TXN.replace('"Shop" "lunch"', '"lunch"'),
        TXN.replace("#t ^l", "#t #u ^l"),
    ):
        assert key(changed) != key(TXN), changed


def test_key_of_text_is_none_unless_the_text_is_one_entry() -> None:
    assert entry_keys.key_of_text(TXN + TXN, "/l/main.bean") is None
    assert entry_keys.key_of_text("2024-01-05 open nope\n", "/l/main.bean") is None
    assert entry_keys.key_of_text("; nothing\n", "/l/main.bean") is None


LEDGER = (
    "2024-01-01 open Assets:Broker\n"
    "2024-01-01 open Assets:Pay USD\n"
    '2024-01-02 * "buy"\n'
    "  Assets:Broker  1 HOO {10 USD}\n"
    "  Assets:Pay\n"
    '2024-01-03 * "sell"\n'
    "  Assets:Broker  -1 HOO {11 USD}\n"
    "  Assets:Pay\n"
    "2024-01-10 balance Assets:Pay  5 USD\n"
)


def error_keys(text: str) -> list[str]:
    _, errors, _ = loader.load_string(text)
    return sorted(entry_keys.error_key(r) for r in rows.error_rows(errors))


def test_an_error_s_key_leaves_out_locations_even_inside_its_message() -> None:
    """The booking error prints its posting, `'lineno': 7` with it; moved down three lines it is the same error."""
    _, errors, _ = loader.load_string(LEDGER)
    assert any("'lineno': 7" in e.message for e in errors)
    assert error_keys(LEDGER) == error_keys("\n\n\n" + LEDGER)
    assert len(error_keys(LEDGER)) == 2


def test_an_error_s_key_changes_with_its_type_entry_or_message() -> None:
    base = error_keys(LEDGER)
    assert error_keys(LEDGER.replace("5 USD", "6 USD")) != base  # the message and the entry
    assert error_keys(LEDGER.replace('"sell"', '"sold"')) != base  # the entry only
    row = rows.error_rows(loader.load_string(LEDGER)[1])[0]
    assert entry_keys.error_key(row) != entry_keys.error_key((*row[:5], "OtherError"))


def test_a_file_and_line_in_a_message_are_left_out_for_the_error_s_own_file() -> None:
    row = (1, "/l/main.bean", 3, "bad at /l/main.bean:3 here", None, "PluginError")
    moved = (1, "/l/main.bean", 9, "bad at /l/main.bean:9 here", None, "PluginError")
    other = (1, "/l/main.bean", 9, "bad at /l/other.bean:9 here", None, "PluginError")
    assert entry_keys.error_key(row) == entry_keys.error_key(moved)
    assert entry_keys.error_key(row) != entry_keys.error_key(other)


def test_tags_and_links_count_as_sets() -> None:
    assert key(TXN.replace("#t ^l", "#t #u ^l")) == key(TXN.replace("#t ^l", "#u #t ^l"))


def test_what_the_parser_does_not_tell_apart_has_one_key() -> None:
    """A metadata value written as an account or a currency, or as a string, is one string to Beancount: one key.
    Step 2b holds those bytes (`test_contract.py::test_a_metadata_value_s_spelling_is_held_by_the_bytes`)."""
    for bare, quoted in (("Assets:Foo", '"Assets:Foo"'), ("USD", '"USD"')):
        a = "2024-01-01 open Assets:Cash\n  xx: " + bare + "\n"
        b = "2024-01-01 open Assets:Cash\n  xx: " + quoted + "\n"
        assert key(a) == key(b)


def test_a_payee_written_as_empty_is_not_no_payee() -> None:
    """S0116 round 1 F4: Beancount's printer writes both as `* "lunch"`; the parse holds `""` and None."""
    assert key(TXN.replace('"Shop" "lunch"', '"" "lunch"')) != key(TXN.replace('"Shop" "lunch"', '"lunch"'))
