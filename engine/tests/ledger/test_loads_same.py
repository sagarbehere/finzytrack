"""What a copy's "loads the same" check compares (`ledger_engine.ledger.loads_same`; `design/specs/engine-jobs.md`
§8.2, the copy; S0081), with Beancount's full load of small ledgers in scratch folders. Written from the owner's
decision 3 of 2026-11-07: "The "loads the same" check then confirms that the copy has the same entries as the source.
Decide and justify the exact comparison against the spec: entries, errors, options, and what to ignore (for example,
the documents path itself and file names in metadata). Because the source ledger may have Beancount errors …, "the
same" means the same as the source, not error-free."
"""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import overlay
from ledger_engine.ledger.loads_same import (
    Difference,
    UncomparableError,
    canonical,
    first_difference,
    summary,
)
from ledger_engine.ledger.policy import DESKTOP

LEDGER = (
    'option "title" "Books"\n'
    "2026-01-01 open Assets:Cash EUR\n"
    "2026-01-01 open Expenses:Food EUR\n"
    '2026-01-05 * "Lunch" #food #work\n'
    "  Expenses:Food  12.50 EUR\n"
    "  Assets:Cash\n"
    "2026-01-06 balance Assets:Cash 3 EUR\n"  # a Beancount error, kept by the copy
)


def load(folder: Path, text: str) -> overlay.Loaded:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "main.beancount").write_text(text)
    out = overlay.load(str(folder / "main.beancount"), {}, DESKTOP)
    assert isinstance(out, overlay.Loaded)
    return out


def mapped(src: Path, dst: Path) -> Callable[[str], str]:
    return lambda s: s.replace(str(dst), str(src))


def same(s: str) -> str:
    return s


def test_the_same_text_in_another_folder_loads_the_same_errors_included(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    assert len(a.errors) == 1, "the source has a Beancount error"
    b = load(tmp_path / "b", LEDGER)
    assert first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b"))) is None


def test_without_mapping_the_file_names_differ(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER)
    assert first_difference(summary(a, same), summary(b, same)) is not None, "file names in metadata are compared"


def test_a_changed_amount_is_an_entry_difference_at_its_file_and_line(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER.replace("12.50", "12.51"))
    d = first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b")))
    assert d == Difference("entry", str(tmp_path / "a" / "main.beancount"), 4)


def test_a_changed_error_is_an_error_difference(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER.replace("balance Assets:Cash 3 EUR", "balance Assets:Cash 4 EUR"))
    d = first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b")))
    assert d is not None
    assert d.what in {"entry", "error"}


def test_an_error_only_in_the_copy_is_a_difference(tmp_path: Path) -> None:
    good = LEDGER.replace("2026-01-06 balance Assets:Cash 3 EUR\n", "")
    a = load(tmp_path / "a", good)
    b = load(tmp_path / "b", good + 'include "missing.beancount"\n')
    assert first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b"))) is not None


def test_a_changed_option_is_an_options_difference(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER.replace('"Books"', '"Other"'))
    d = first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b")))
    assert d == Difference("options")


def test_an_extra_entry_in_the_copy_is_a_difference(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER + "2026-02-01 open Assets:Bank EUR\n")
    assert first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b"))) is not None


def test_the_input_hash_is_not_compared(tmp_path: Path) -> None:
    a = load(tmp_path / "a", LEDGER)
    b = load(tmp_path / "b", LEDGER)
    b.options["input_hash"] = "something else"
    assert first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b"))) is None


def test_a_set_compares_whatever_its_order() -> None:
    assert canonical(frozenset({"b", "a", "c"}), same) == canonical(frozenset({"c", "b", "a"}), same)
    assert canonical({"x": Decimal("1.0")}, same) != canonical({"x": Decimal("1.00")}, same)


def test_a_value_of_an_unknown_kind_is_never_compared_by_guess() -> None:
    class PluginThing:
        pass

    with pytest.raises(UncomparableError):
        canonical({"meta": PluginThing()}, same)


def test_a_custom_directive_is_compared_by_its_values_and_their_types(tmp_path: Path) -> None:
    # S0095: a `custom` entry's values are `ValueType(value, dtype)`, and `dtype` is a class (`str`, `Amount`,
    # `Decimal`, ...) or Beancount's account marker. Refusing a class as an unknown kind refused every ledger with a
    # `custom` directive (Fava's options, budgets), v0.2.2's own demo ledger among them.
    text = (
        "2026-01-01 open Assets:Cash EUR\n"
        '2026-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\n'
        '2026-01-01 custom "budget" Assets:Cash "monthly" 90 EUR TRUE 2026-02-01 3.5\n'
    )
    a = load(tmp_path / "a", text)
    b = load(tmp_path / "b", text)
    assert first_difference(summary(a, same), summary(b, mapped(tmp_path / "a", tmp_path / "b"))) is None
    other = load(tmp_path / "c", text.replace('"monthly" 90 EUR', '"monthly" 91 EUR'))
    assert first_difference(summary(a, same), summary(other, mapped(tmp_path / "a", tmp_path / "c"))) is not None


def test_a_class_is_compared_only_when_the_parser_records_it_as_a_value_type() -> None:
    # S0095 red team F1: comparing any class by its name let a plugin's class with other contents, or one named like
    # decimal.Decimal, compare equal. Only the parser's own value types compare, by identity.
    assert canonical(str, same) == canonical(str, same)
    assert canonical(str, same) != canonical(bool, same)
    assert canonical(decimal.Decimal, same) == ["c", "Decimal"]
    # Beancount's only other class in a load: the marker of an amount left out, kept in an entry whose booking failed.
    assert [port.PARSER_CLASSES[c] for c in set(port.PARSER_CLASSES) - set(port.CUSTOM_VALUE_TYPES)] == ["MISSING"]
    spoofed = type("Decimal", (), {"__module__": "decimal"})
    assert spoofed.__qualname__ == decimal.Decimal.__qualname__
    for other in (spoofed, type("Rate", (), {"value": "1.10"}), int, float, datetime.datetime, type, list[int]):
        with pytest.raises(UncomparableError):
            canonical(other, same)


def test_the_value_types_are_what_the_parser_gives(tmp_path: Path) -> None:
    # Every value form of Beancount 3.2.0's `custom` grammar: a string, an account, a number (and an expression), an
    # amount, a date, a flag. The classes it records must be exactly the comparison's list.
    a = load(
        tmp_path / "a",
        "2026-01-01 open Assets:Cash EUR\n"
        '2026-01-01 custom "k" "s" Assets:Cash 1.5 2 + 3 1.00 EUR 2 * 3 EUR 2026-02-01 TRUE FALSE\n',
    )
    assert a.errors == []
    dtypes = [v.dtype for e in a.entries if isinstance(e, port.Custom) for v in e.values]
    assert {d for d in dtypes if isinstance(d, type)} == set(port.CUSTOM_VALUE_TYPES)
    assert [d for d in dtypes if not isinstance(d, type)] == ["<AccountDummy>"]
