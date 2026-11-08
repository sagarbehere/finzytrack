"""The pinned ISO 4217 cash list (BL-0120; the owner's answer of 2026-10-29,
design/inbox/answered/20261028-iso-currency-list.md) and the script that derives it from the ISO 4217 Maintenance
Agency's List One and List Three (`engine/scripts/gen_iso4217.py`).

The pinned module must be exactly what the script renders from its own recorded source and its two sets, so nobody
edits a code in or out by hand. The derivation rules are tested on small synthetic lists in the Agency's format.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ledger_engine.mirror.core import iso4217

ENGINE = Path(__file__).resolve().parents[2]
# The Agency's two files, as downloaded, in a folder outside the repository (owner, 2026-11-08: "Keep out of the
# repo"; their licence is not stated). The source test runs only where they are, like the T-local tier.
SOURCES_ENV = "FT_ISO4217_DIR"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_iso4217", ENGINE / "scripts" / "gen_iso4217.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_iso4217"] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


gen = load_script()


# ---- the pinned list ------------------------------------------------------------------------------------------------


def test_the_pin_records_where_it_came_from() -> None:
    assert iso4217.LIST_ONE.url == gen.URL_ONE
    assert iso4217.LIST_THREE.url == gen.URL_THREE
    for value in (iso4217.RETRIEVED, iso4217.LIST_ONE.published, iso4217.LIST_THREE.published):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), value
    for digest in (iso4217.LIST_ONE.sha256, iso4217.LIST_THREE.sha256):
        assert re.fullmatch(r"[0-9a-f]{64}", digest), digest


def test_the_pinned_codes() -> None:
    assert all(re.fullmatch(r"[A-Z]{3}", c) for c in iso4217.CASH | set(iso4217.NOT_CASH))
    assert not iso4217.CASH & set(iso4217.NOT_CASH)
    assert all(reason.strip() for reason in iso4217.NOT_CASH.values())
    # List One of 2026-09-17 and List Three of 2026-01-01: 307 distinct codes, 277 a country's money.
    assert (len(iso4217.CASH), len(iso4217.NOT_CASH)) == (277, 30)


def test_the_pinned_module_is_what_the_script_renders() -> None:
    one = gen.Source(iso4217.LIST_ONE.url, iso4217.LIST_ONE.published, iso4217.LIST_ONE.sha256)
    three = gen.Source(iso4217.LIST_THREE.url, iso4217.LIST_THREE.published, iso4217.LIST_THREE.sha256)
    text = gen.render(one, three, iso4217.RETRIEVED, sorted(iso4217.CASH), dict(iso4217.NOT_CASH))
    assert iso4217.__file__ is not None
    assert Path(iso4217.__file__).read_text(encoding="utf-8") == text


def test_the_pin_is_the_derivation_of_the_agencys_files() -> None:
    # S0086 verifier F1: the files are the ones the pin names (their SHA-256), and the module is exactly what the
    # script derives from them, so no code can be edited in or out by hand. Skipped where the files are not.
    folder = os.environ.get(SOURCES_ENV)
    if not folder:
        pytest.skip(f"{SOURCES_ENV} is not set: the ISO 4217 List One and List Three files are kept outside the repo")
    sources = Path(folder).expanduser()
    if not (sources / "list-one.xml").is_file() or not (sources / "list-three.xml").is_file():
        pytest.skip(f"{SOURCES_ENV}={folder} holds no list-one.xml and list-three.xml")
    one = (sources / "list-one.xml").read_bytes()
    three = (sources / "list-three.xml").read_bytes()
    assert hashlib.sha256(one).hexdigest() == iso4217.LIST_ONE.sha256
    assert hashlib.sha256(three).hexdigest() == iso4217.LIST_THREE.sha256
    assert iso4217.__file__ is not None
    assert Path(iso4217.__file__).read_text(encoding="utf-8") == gen.derive(one, three, iso4217.RETRIEVED)


def test_the_hand_classified_codes_are_pinned_as_classified() -> None:
    for code in (*gen.ACCOUNTING_UNITS, *gen.NOT_MONEY):
        assert code in iso4217.NOT_CASH, code
    for code in gen.X_COUNTRY_MONEY:
        assert code in iso4217.CASH, code


# ---- the derivation rules, on synthetic lists in the Agency's format ----------------------------------------------

# Every code the script classifies by hand must be in the lists, or the derivation stops; the base lists hold them.
HAND = sorted({*gen.ACCOUNTING_UNITS, *gen.NOT_MONEY, *gen.X_COUNTRY_MONEY})


def entry(code: str, country: str = "ATLANTIS", name: str = "Coin", fund: str | None = None, historic: bool = False):
    tag = "HstrcCcyNtry" if historic else "CcyNtry"
    flag = "" if fund is None else f' IsFund="{fund}"'
    ccy = "" if not code else f"<Ccy>{code}</Ccy>"
    return f"<{tag}><CtryNm>{country}</CtryNm><CcyNm{flag}>{name}</CcyNm>{ccy}</{tag}>"


def lists(one: list[str], three: list[str]) -> tuple[bytes, bytes]:
    base = [entry(c, country="SOMEWHERE") for c in HAND]
    list_one = f'<ISO_4217 Pblshd="2026-09-17"><CcyTbl>{"".join(base + one)}</CcyTbl></ISO_4217>'
    list_three = f'<ISO_4217 Pblshd="2026-01-01"><HstrcCcyTbl>{"".join(three)}</HstrcCcyTbl></ISO_4217>'
    return list_one.encode(), list_three.encode()


def derived(one: list[str], three: list[str] | None = None) -> tuple[frozenset[str], dict[str, str]]:
    list_one, list_three = lists(one, three or [])
    _, current = gen.parse(list_one, "CcyNtry")
    _, historic = gen.parse(list_three, "HstrcCcyNtry")
    cash, other = gen.classify(current + historic)
    return frozenset(cash), other


def test_a_countrys_current_and_historic_money_is_cash() -> None:
    cash, other = derived([entry("AAA")], [entry("BBB", historic=True)])
    assert {"AAA", "BBB"} <= cash
    assert "AAA" not in other


def test_a_code_iso_flags_as_a_fund_is_not_cash_in_either_spelling() -> None:
    cash, other = derived([entry("FFA", fund="true")], [entry("FFB", fund="WAHR", historic=True)])
    assert not {"FFA", "FFB"} & cash
    assert other["FFA"].startswith("fund")
    assert other["FFB"].startswith("fund")


def test_a_non_country_entry_is_not_cash() -> None:
    cash, other = derived([entry("XAU", country="ZZ08_Gold", name="Gold")])
    assert "XAU" not in cash
    assert other["XAU"] == "ISO non-country entry: Gold"


def test_the_hand_classified_codes() -> None:
    cash, other = derived([])
    assert set(gen.X_COUNTRY_MONEY) <= cash
    assert set(gen.ACCOUNTING_UNITS) | set(gen.NOT_MONEY) <= set(other)


def test_an_entry_without_a_code_is_skipped() -> None:
    cash, other = derived([entry("", country="ANTARCTICA", name="No universal currency")])
    assert len(cash) + len(other) == len(HAND)


@pytest.mark.parametrize(
    ("one", "three", "message"),
    [
        ([entry("XNW", country="NEWLAND")], [], "an X code not yet classified"),
        ([entry("MIX"), entry("MIX", fund="true")], [], "a fund in some entries and not in others"),
        ([entry("FLG", fund="yes")], [], "unknown IsFund value"),
        ([entry("ZZC"), entry("ZZC", country="ZZ99_Thing")], [], "non-country (ZZ) entry"),
        ([entry("usd")], [], "not three capital letters"),
    ],
)
def test_what_the_rule_cannot_place_stops_the_derivation(one: list[str], three: list[str], message: str) -> None:
    with pytest.raises(gen.DerivationError, match=re.escape(message)):
        gen.derive(*lists(one, three), "2026-11-08")


def test_a_hand_classified_code_missing_from_both_lists_stops_the_derivation() -> None:
    one = f'<ISO_4217 Pblshd="2026-09-17"><CcyTbl>{entry("AAA")}</CcyTbl></ISO_4217>'.encode()
    three = b'<ISO_4217 Pblshd="2026-01-01"><HstrcCcyTbl></HstrcCcyTbl></ISO_4217>'
    with pytest.raises(gen.DerivationError, match="classified by hand but in neither list"):
        gen.derive(one, three, "2026-11-08")


def test_a_list_without_a_publication_date_stops_the_derivation() -> None:
    one, three = lists([], [])
    with pytest.raises(gen.DerivationError, match="publication date"):
        gen.derive(one.replace(b' Pblshd="2026-09-17"', b""), three, "2026-11-08")
