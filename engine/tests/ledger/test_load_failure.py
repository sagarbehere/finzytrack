"""A ledger Beancount's own load fails on is refused, never raised (S0049's contract; BL-0123; S0098).

Beancount 3.2.0 raises out of its load in two known ways, and bean-check fails on both: a number its 28-digit
arithmetic cannot quantize (an amount of more than 28 digits whose other leg it must fill in: `decimal.
InvalidOperation`), and an entry a plugin made that its own checks cannot read (a `document` whose file name is not
text: `TypeError` in `validate_documents_paths`). The overlay load answers `LoadRefused("number-failed" |
"beancount-failed", file, line)`: the file and line of the entry Beancount was working on when one is known, else the
root. A disk error (`OSError`) and an encrypted file met keep their own paths.
"""

from __future__ import annotations

import decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import load_failure
from ledger_engine.ledger.overlay import Loaded, LoadRefused, load
from ledger_engine.ledger.policy import DESKTOP

pytestmark = pytest.mark.inv("INV-22")

OPEN = "2020-01-01 open Assets:Wallet\n2020-01-01 open Equity:Opening\n"
CRASH = '2021-01-01 * "buy"\n  Assets:Wallet  12345678901.123456789012345678 ETH\n  Equity:Opening\n'
DOC_PLUGIN = (
    "import datetime\nfrom beancount.core import data\n__plugins__ = ['add']\n"
    "def add(entries, options):\n"
    "    meta = data.new_metadata('<plugin>', 0)\n"
    "    doc = data.Document(meta, datetime.date(2020, 1, 2), 'Assets:Wallet', 42, frozenset(), frozenset())\n"
    "    return entries + [doc], []\n"
)


def write(tmp_path: Path, text: str, plugin: str | None = None) -> Path:
    root = tmp_path / "main.beancount"
    head = ""
    if plugin is not None:
        name = f"plug_{tmp_path.name.replace('-', '_')}"  # one module name per test: no import is reused
        (tmp_path / f"{name}.py").write_text(plugin, encoding="utf-8")
        head = f'option "insert_pythonpath" "TRUE"\nplugin "{name}"\n'
    root.write_text(head + text, encoding="utf-8")
    return root


def test_beancounts_own_load_raises_on_both_cases(tmp_path: Path) -> None:
    """The premise: Beancount's loader (as bean-check runs it) raises on both ledgers."""
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    with pytest.raises(decimal.InvalidOperation):
        port.real_load(str(write(tmp_path / "a", OPEN + CRASH)))
    with pytest.raises(TypeError):
        port.real_load(str(write(tmp_path / "b", OPEN, DOC_PLUGIN)))


def test_a_number_beancount_cannot_quantize_is_refused_naming_its_line(tmp_path: Path) -> None:
    root = write(tmp_path, OPEN + CRASH)
    assert load(str(root), {}, DESKTOP) == LoadRefused("number-failed", str(root), 3)


def test_the_line_named_is_in_the_file_that_holds_it(tmp_path: Path) -> None:
    (tmp_path / "inc.beancount").write_text("\n" + CRASH, encoding="utf-8")
    root = write(tmp_path, OPEN + 'include "inc.beancount"\n')
    assert load(str(root), {}, DESKTOP) == LoadRefused("number-failed", str(tmp_path / "inc.beancount"), 2)


def test_a_plugins_document_beancount_cannot_check_is_refused(tmp_path: Path) -> None:
    root = write(tmp_path, OPEN, DOC_PLUGIN)
    # The entry Beancount failed on is not known (its check is one expression over every entry), and the plugin's
    # entry names no ledger file: the root is named, with no line.
    assert load(str(root), {}, DESKTOP) == LoadRefused("beancount-failed", str(root), None)


def test_a_plugin_that_fails_while_it_is_imported_is_refused(tmp_path: Path) -> None:
    root = write(tmp_path, OPEN, "1 / 0\n")
    assert load(str(root), {}, DESKTOP) == LoadRefused("beancount-failed", str(root), None)


# ---- must not pass: what is not Beancount failing on the ledger ------------------------------------------------------


def test_a_ledger_beancount_loads_is_never_refused(tmp_path: Path) -> None:
    root = write(
        tmp_path, OPEN + '2021-01-01 * "buy"\n  Assets:Wallet  1234567890.123456789012345678 ETH\n  Equity:Opening\n'
    )
    assert isinstance(load(str(root), {}, DESKTOP), Loaded)


def test_a_plugin_that_raises_when_called_is_beancounts_error_not_a_refusal(tmp_path: Path) -> None:
    # Beancount catches an exception from a plugin's function and reports it as a load error: the load returns.
    root = write(tmp_path, OPEN, "__plugins__ = ['boom']\ndef boom(entries, options):\n    raise ValueError('x')\n")
    out = load(str(root), {}, DESKTOP)
    assert isinstance(out, Loaded), out
    assert any("boom" in e.message or "ValueError" in e.message for e in out.errors)


def test_a_disk_error_during_the_load_is_raised_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = write(tmp_path, OPEN)

    def fails(*_: object) -> list[port.BeancountError]:
        raise PermissionError(13, "denied", str(root))

    monkeypatch.setattr(port, "validate", fails)
    with pytest.raises(PermissionError):
        load(str(root), {}, DESKTOP)


def _raise(entry: object) -> None:
    """Raises while this frame holds `entry` under that name, as Beancount's loops do."""
    msg = f"failed on a {type(entry).__name__}"
    raise ValueError(msg)


def fail_on(entry: object) -> Exception:
    try:
        _raise(entry)
    except ValueError as e:
        return e
    msg = "no exception"
    raise AssertionError(msg)


def test_the_entry_beancount_was_working_on_is_named(tmp_path: Path) -> None:
    root = str(tmp_path / "main.beancount")
    other = str(tmp_path / "other.beancount")
    got = load_failure.of(fail_on(SimpleNamespace(meta={"filename": other, "lineno": 7})), root)
    assert (got.path, got.line) == (other, 7)


def test_an_entry_that_names_no_ledger_file_is_never_named(tmp_path: Path) -> None:
    root = str(tmp_path / "main.beancount")
    for meta in (
        {"filename": "<plugin>", "lineno": 3},
        {"filename": "relative.bean", "lineno": 3},
        {"filename": 42, "lineno": 3},
        {"filename": str(tmp_path / "x.bean"), "lineno": 0},
        {"filename": str(tmp_path / "x.bean"), "lineno": "3"},
        {"filename": str(tmp_path / "x.bean"), "lineno": True},
        {},
        None,
    ):
        got = load_failure.of(fail_on(SimpleNamespace(meta=meta)), root)
        assert (got.path, got.line) == (root, None), meta


def test_a_decimal_error_is_a_number_failure_and_anything_else_is_beancounts(tmp_path: Path) -> None:
    root = str(tmp_path / "main.beancount")
    assert load_failure.of(decimal.InvalidOperation(), root).reason == "number-failed"
    assert load_failure.of(decimal.DivisionByZero(), root).reason == "number-failed"
    assert load_failure.of(TypeError(), root).reason == "beancount-failed"
    assert load_failure.of(ZeroDivisionError(), root).reason == "beancount-failed"
