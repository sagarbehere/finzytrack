"""P1 integrity tests for the kernel parsers (L3-11) and NL transaction checks (L6-12), both due by M6.

review:27 §1(d), Appendix B (both in M6).

L3-11. Spec: review:L3 L3-11 (the legacy parsers turned "1,234.00 Dr" into +1234, a Unicode minus sign
(U+2212) before 45.00 into +45, "1.23E+05" into 1.2305, dropped "1.234,56-", and accepted 2026-02-31); its fix:
one `parse_amount` with explicit
negative styles (`dr_cr_suffix`, `trailing_minus`, `parentheses`) and strict calendar validation, the written scale
kept (INV-06); review:L4 §1.5 (a debit is money out: negative on the anchor; formats are explicit, never a candidate
list; an invalid date is a rejected row with its reason). Assumed, not decided: `app.domain.parse` with
`parse_amount(text, style) -> Decimal` and `parse_date(text, fmt) -> date`, both raising `ParseError`, and the style
object `AmountStyle(decimal_mark=…, negative=…)`. The exponent case is not decided by the spec: it may parse exactly or
be refused, but never mean 1.2305.

L6-12. Spec: review:L6 L6-12 (residuals summed across currencies: -100 USD / +100 EUR passed and +10 AAPL / -1500 USD
was reported unbalanced; the default currency was the alphabetically first commodity, e.g. AAPL), its fix: residuals
per currency with costs and prices from the kernel, the default currency from the Ledger Profile; ADR-0052 (the
operating currency is a fact in the ledger); ADR-0033 (AI ledger changes are previewed plans). Assumed, not decided:
`app.importing.core.nl.check(draft, profile) -> Check` with `.balanced` and `.residuals` (currency -> Decimal), and
`app.importing.core.nl.from_model(payload, profile)` turning the model's JSON into a Draft.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from tests.integrity.planned import planned

PARSE = "app.domain.parse"


@pytest.mark.fixme(id="L3-11", due="M6", reason="capability not built")
@pytest.mark.parametrize(
    ("text", "style", "expected"),
    [
        ("1,234.00 Dr", {"negative": "dr_cr_suffix"}, Decimal("-1234.00")),
        ("1,234.00 Cr", {"negative": "dr_cr_suffix"}, Decimal("1234.00")),
        ("\N{MINUS SIGN}45.00", {}, Decimal("-45.00")),
        ("(45.00)", {"negative": "parentheses"}, Decimal("-45.00")),
        ("1.234,56-", {"negative": "trailing_minus", "decimal_mark": ","}, Decimal("-1234.56")),
        ("1.234,56-", {"decimal_mark": ","}, None),  # trailing minus not declared: refused, never positive or dropped
        ("10.50", {}, Decimal("10.50")),
    ],
)
def test_amounts_keep_every_sign_form_and_their_written_scale(text: str, style: dict[str, str], expected: Any) -> None:
    parse_amount = planned(PARSE, "parse_amount")
    parse_error = planned(PARSE, "ParseError")
    amount_style = planned(PARSE, "AmountStyle")
    if expected is None:
        with pytest.raises(parse_error):
            parse_amount(text, amount_style(**style))
        return
    got = parse_amount(text, amount_style(**style))
    assert got == expected
    assert got.as_tuple().exponent == expected.as_tuple().exponent  # 10.50 stays 10.50 (INV-06)


@pytest.mark.fixme(id="L3-11", due="M6", reason="capability not built")
def test_impossible_dates_are_refused_and_month_names_parse() -> None:
    parse_date = planned(PARSE, "parse_date")
    parse_error = planned(PARSE, "ParseError")
    parse_amount = planned(PARSE, "parse_amount")
    amount_style = planned(PARSE, "AmountStyle")

    assert parse_date("05-Oct-2026", "%d-%b-%Y") == date(2026, 10, 5)
    for text, fmt in (("2026-02-31", "%Y-%m-%d"), ("31/09/2026", "%d/%m/%Y")):
        with pytest.raises(parse_error):
            parse_date(text, fmt)
    try:
        exponent = parse_amount("1.23E+05", amount_style())
    except parse_error:
        exponent = None
    assert exponent in (None, Decimal("123000"))


def _nl_draft(from_model: Any, payload: dict[str, Any], profile: Any) -> Any:
    return from_model(payload, profile)


@pytest.mark.fixme(id="L6-12", due="M6", reason="capability not built")
def test_nl_transaction_checks_are_per_currency_and_default_to_the_profile_currency() -> None:
    check = planned("app.importing.core.nl", "check")
    from_model = planned("app.importing.core.nl", "from_model")
    ledger_profile = planned("app.accounts.core", "LedgerProfile")
    profile = ledger_profile.for_test(operating_currencies=("INR",), commodities=("AAPL", "EUR", "INR", "USD"))
    fx = {
        "date": "2026-03-04",
        "postings": [
            {"account": "Assets:Usd", "amount": "-100", "currency": "USD"},
            {"account": "Assets:Eur", "amount": "100", "currency": "EUR"},
        ],
    }
    buy = {
        "date": "2026-03-04",
        "postings": [
            {
                "account": "Assets:Broker:AAPL",
                "amount": "10",
                "currency": "AAPL",
                "cost": {"number": "150", "currency": "USD"},
            },
            {"account": "Assets:Broker:Cash", "amount": "-1500", "currency": "USD"},
        ],
    }
    no_currency = {
        "date": "2026-03-04",
        "postings": [{"account": "Expenses:Food", "amount": "250"}, {"account": "Assets:Bank", "amount": "-250"}],
    }

    unbalanced = check(_nl_draft(from_model, fx, profile), profile)
    assert not unbalanced.balanced
    assert unbalanced.residuals == {"USD": Decimal("-100"), "EUR": Decimal("100")}
    assert check(_nl_draft(from_model, buy, profile), profile).balanced  # 10 AAPL at cost 150 USD = 1500 USD
    draft = _nl_draft(from_model, no_currency, profile)
    assert {p.units.currency for p in draft.postings} == {"INR"}  # the profile's, never the first commodity (AAPL)
