"""Derive the engine's pinned ISO 4217 cash list from the Maintenance Agency's own files (S0086, BL-0120).

usage (cwd = engine/):
  .venv/bin/python -I scripts/gen_iso4217.py "$FT_ISO4217_DIR/list-one.xml" "$FT_ISO4217_DIR/list-three.xml" \
      RETRIEVED > src/ledger_engine/mirror/core/iso4217.py

The two files are ISO 4217 List One (current codes) and List Three (historic codes), downloaded from the ISO 4217
Maintenance Agency (SIX Financial Information AG) at `URL_ONE` and `URL_THREE` and kept as published in a folder
outside the repository named by `FT_ISO4217_DIR` (owner, 2026-11-08: their licence is not stated); RETRIEVED is the
download date (YYYY-MM-DD). The script prints the module; it records each file's publication date and SHA-256, and
`tests/mirror/test_iso4217.py` derives the module from the files again when that folder is set. Nothing in the engine
fetches anything at run time.

The rule is the owner's (2026-10-29, `design/inbox/answered/20261028-iso-currency-list.md`, option 2): every code that
was ever a country's money, current or historic, is cash; precious metals, funds and accounting units are not (the
ledger's operating currencies, `asset-class` and lots held at cost decide before this list, in `rows.py`). Per code,
over every entry of both lists:
  1. a code named in `ACCOUNTING_UNITS` or `NOT_MONEY` is not cash, for the reason given there;
  2. a code ISO flags as a fund (`IsFund`, spelt `true` in List One and `WAHR` in List Three) is not cash;
  3. a code whose entries are all ISO's non-country entries (country name `ZZnn_...`: metals, bond-market units,
     the test and no-currency codes, Gold-Franc, RINET, UIC-Franc) is not cash;
  4. a code in the `X` range (ISO's range for codes that are not one country's) is cash only when listed in
     `X_COUNTRY_MONEY` (money that countries share, such as the CFA francs); any other `X` code stops the derivation;
  5. every other code is a country's money: cash.
Anything the rule cannot place (a fund flag on some entries of a code and not others, an unknown flag value, a code
both a country's and a `ZZ` entry, a new `X` code) stops the derivation with a message, so an update of the lists
never classifies a new kind of code silently.
"""

from __future__ import annotations

import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# The Agency's own paths, "iso-currrency" (three r's) included: that is how SIX spells them.
URL_ONE = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml"
URL_THREE = (
    "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-three.xml"
)
FUND_FLAGS = frozenset({"true", "WAHR"})  # List Three spells it in German
CODE = re.compile(r"[A-Z]{3}")

# Units of account of international bodies: no country's money (owner: "accounting units are holdings").
ACCOUNTING_UNITS = {
    "XDR": "accounting unit: the IMF's Special Drawing Right",
    "XUA": "accounting unit: the African Development Bank's unit of account",
    "XSU": "accounting unit: the SUCRE regional payment system's unit",
    "XEU": "accounting unit: the European Currency Unit (ECU), a basket, never notes or coins",
}
# Entries under a country that are not that country's money, though List Three does not flag them; each has a sibling
# that ISO does flag as a fund (owner: "funds ... are holdings").
NOT_MONEY = {
    "ECV": "accounting unit: Ecuador's indexed Unidad de Valor Constante (as CLF, which ISO flags a fund)",
    "USS": "fund: US Dollar (Same day), a settlement variant (as USN, which ISO flags a fund)",
    "CHC": "fund: WIR Franc (for electronic), the WIR Bank's money (as CHE, CHW, which ISO flags funds)",
}
# `X` codes that are money countries use (a monetary union's or a territory's legal tender).
X_COUNTRY_MONEY = {
    "XAF": "CFA Franc BEAC (Central African states)",
    "XCD": "East Caribbean Dollar",
    "XCG": "Caribbean Guilder (Curaçao, Sint Maarten)",
    "XOF": "CFA Franc BCEAO (West African states)",
    "XPF": "CFP Franc (French Pacific territories)",
}


class DerivationError(Exception):
    """The lists hold a code the rule cannot place; a person must classify it (and record why) first."""


@dataclass(frozen=True, slots=True)
class Entry:
    code: str
    country: str
    name: str
    fund: bool


@dataclass(frozen=True, slots=True)
class Source:
    url: str
    published: str
    sha256: str


def parse(data: bytes, entry_tag: str) -> tuple[str, list[Entry]]:
    """The list's publication date and its entries that carry a code (an entry such as Antarctica's has none)."""
    root = ET.fromstring(data)  # noqa: S314 (the Agency's file, read by a person's command, never at run time)
    published = root.get("Pblshd")
    if not published:
        msg = "the list has no publication date (Pblshd)"
        raise DerivationError(msg)
    out: list[Entry] = []
    for node in root.iter(entry_tag):
        code = (node.findtext("Ccy") or "").strip()
        if not code:
            continue
        if not CODE.fullmatch(code):
            msg = f"{code!r} is not three capital letters"
            raise DerivationError(msg)
        name = node.find("CcyNm")
        flag = None if name is None else name.get("IsFund")
        if flag is not None and flag not in FUND_FLAGS:
            msg = f"{code}: unknown IsFund value {flag!r}"
            raise DerivationError(msg)
        country = " ".join((node.findtext("CtryNm") or "").split())
        out.append(
            Entry(code, country, " ".join((name.text or "").split()) if name is not None else "", flag is not None)
        )
    return published, out


def classify(entries: list[Entry]) -> tuple[list[str], dict[str, str]]:
    """Cash codes (sorted) and the other codes with their reason."""
    by_code: dict[str, list[Entry]] = {}
    for e in entries:
        by_code.setdefault(e.code, []).append(e)
    cash: list[str] = []
    other: dict[str, str] = {}
    for code, group in sorted(by_code.items()):
        funds = {e.fund for e in group}
        non_country = {e.country.startswith("ZZ") for e in group}
        if code in ACCOUNTING_UNITS:
            other[code] = ACCOUNTING_UNITS[code]
        elif code in NOT_MONEY:
            other[code] = NOT_MONEY[code]
        elif len(funds) > 1:
            msg = f"{code}: a fund in some entries and not in others; classify it by hand"
            raise DerivationError(msg)
        elif funds == {True}:
            other[code] = f"fund (ISO flags it): {group[0].name}"
        elif len(non_country) > 1:
            msg = f"{code}: both a country's entry and a non-country (ZZ) entry; classify it by hand"
            raise DerivationError(msg)
        elif non_country == {True}:
            other[code] = f"ISO non-country entry: {group[0].name}"
        elif code.startswith("X") and code not in X_COUNTRY_MONEY:
            names = sorted({f"{e.country}: {e.name}" for e in group})
            msg = f"{code}: an X code not yet classified ({'; '.join(names)})"
            raise DerivationError(msg)
        else:
            cash.append(code)
    if missing := sorted((set(ACCOUNTING_UNITS) | set(NOT_MONEY) | set(X_COUNTRY_MONEY)) - set(by_code)):
        msg = f"classified by hand but in neither list: {', '.join(missing)}"
        raise DerivationError(msg)
    return cash, other


def render(one: Source, three: Source, retrieved: str, cash: list[str], other: dict[str, str]) -> str:
    """The module's text, already in `ruff format`'s shape (the test checks the pinned module equals it)."""
    codes = [" ".join(cash[i : i + 20]) for i in range(0, len(cash), 20)]
    lines = [
        '"""ISO 4217 codes that count as cash in the mirror\'s `commodities.is_currency` (ADR-0025; BL-0120).',
        "",
        "Derived by `engine/scripts/gen_iso4217.py` from the ISO 4217 Maintenance Agency's lists (SIX Financial",
        "Information AG): List One (current codes) and List Three (historic codes). Do not edit by hand: download the",
        "lists again and rerun the script (its docstring has the rule). The owner's rule (2026-10-29): every code that",
        "was ever a country's money, current and historic, is cash; metals, funds and accounting units are not",
        "(`NOT_CASH`, each with its reason). The ledger's operating currencies, `asset-class` and lots held at cost",
        "decide before this list (`rows.is_currency`). Nothing is fetched at run time.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Final",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class Source:",
        '    """One of the Agency\'s files: where it was downloaded, its publication date and its SHA-256."""',
        "",
        "    url: str",
        "    published: str",
        "    sha256: str",
        "",
        "",
        f'RETRIEVED: Final = "{retrieved}"',
        *(
            line
            for name, src in (("LIST_ONE", one), ("LIST_THREE", three))
            for line in (
                f"{name}: Final = Source(",
                f'    url="{src.url}",',
                f'    published="{src.published}",',
                f'    sha256="{src.sha256}",',
                ")",
            )
        ),
        "",
        f"# {len(cash)} codes: every code of both lists that was a country's money.",
        '_CASH_CODES = """',
        *codes,
        '"""',
        "CASH: Final = frozenset(_CASH_CODES.split())",
        "",
        f"# {len(other)} codes of the lists that are not cash, and why.",
        "NOT_CASH: Final = {",
        *(f'    "{code}": "{reason}",' for code, reason in sorted(other.items())),
        "}",
        "",
    ]
    return "\n".join(lines)


def derive(list_one: bytes, list_three: bytes, retrieved: str) -> str:
    published_one, current = parse(list_one, "CcyNtry")
    published_three, historic = parse(list_three, "HstrcCcyNtry")
    cash, other = classify(current + historic)
    one = Source(URL_ONE, published_one, hashlib.sha256(list_one).hexdigest())
    three = Source(URL_THREE, published_three, hashlib.sha256(list_three).hexdigest())
    return render(one, three, retrieved, cash, other)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", argv[2]):
        sys.stderr.write(__doc__ or "")
        return 2
    try:
        sys.stdout.write(derive(Path(argv[0]).read_bytes(), Path(argv[1]).read_bytes(), argv[2]))
    except DerivationError as e:
        sys.stderr.write(f"gen_iso4217: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
