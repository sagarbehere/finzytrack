"""What counts as a valid commodity/currency code at the API boundary.

Beancount owns this rule. A hand-written `^[A-Z0-9]+$` in our schemas rejected
codes Beancount accepts — `ELEC-DAYS`, `BRK.B` — and because the pattern sat on
a *response* model, the failure was not "that commodity is invalid" but
"Error accessing ledger": the whole ledger became unopenable, and a user had to
edit commodities out of their file to start the app.

So these tests assert our pattern matches Beancount's, in both directions: it
must accept everything the parser accepts, and still reject what the parser
rejects.
"""

import re
from datetime import date

import pytest
from beancount.core.amount import CURRENCY_RE
from pydantic import ValidationError

from app.core.constants import COMMODITY_CODE_MAX_LENGTH, COMMODITY_CODE_PATTERN
from app.schemas.account_schemas import AccountCreateRequest
from app.schemas.commodity_schemas import CommodityCreateRequest

# Codes Beancount accepts. The dashed and dotted forms are the ones that broke.
VALID_CODES = [
    "USD",
    "EUR",
    "AAPL",
    "ELEC-DAYS",     # reported by a user; app would not start
    "BRK.B",         # dotted share classes are common
    "X_1",
    "VWRL",
    "A",             # single letter
    "AAPL2",
    "GBP-CASH-ISA",
    "O'NEIL",
]

INVALID_CODES = [
    "usd",           # lowercase
    "1ABC",          # leading digit
    "USD-",          # trailing punctuation
    "US D",          # space
    "",              # empty
    "-USD",          # leading dash
]


class TestPatternMatchesBeancount:
    """Our pattern must agree with the parser's own rule, both ways."""

    @pytest.mark.parametrize("code", VALID_CODES)
    def test_accepts_what_beancount_accepts(self, code):
        assert re.fullmatch(CURRENCY_RE, code), (
            f"test premise wrong: Beancount rejects {code!r}"
        )
        assert re.fullmatch(COMMODITY_CODE_PATTERN, code), (
            f"our pattern rejects {code!r}, which Beancount accepts"
        )

    @pytest.mark.parametrize("code", INVALID_CODES)
    def test_rejects_what_beancount_rejects(self, code):
        assert not re.fullmatch(CURRENCY_RE, code), (
            f"test premise wrong: Beancount accepts {code!r}"
        )
        assert not re.fullmatch(COMMODITY_CODE_PATTERN, code)


class TestCommoditySchema:
    @pytest.mark.parametrize("code", VALID_CODES)
    def test_commodity_create_accepts_valid_codes(self, code):
        assert CommodityCreateRequest(code=code).code == code

    @pytest.mark.parametrize("code", INVALID_CODES)
    def test_commodity_create_rejects_invalid_codes(self, code):
        with pytest.raises(ValidationError):
            CommodityCreateRequest(code=code)

    def test_length_bound_is_not_the_thing_that_rejects_real_codes(self):
        """The old 16-char cap was low enough to hit plausible codes."""
        assert COMMODITY_CODE_MAX_LENGTH >= 32
        code = "A" + "B" * 30
        assert CommodityCreateRequest(code=code).code == code


class TestAccountSchema:
    """Account currencies use the same rule — `open Assets:X ELEC-DAYS`."""

    @pytest.mark.parametrize("code", ["ELEC-DAYS", "BRK.B", "USD"])
    def test_account_create_accepts_valid_codes(self, code):
        request = AccountCreateRequest(
            name="Assets:Broker:Test", open_date=date(2020, 1, 1), currencies=[code]
        )
        assert request.currencies == [code]

    def test_account_create_still_rejects_junk(self):
        with pytest.raises(ValidationError):
            AccountCreateRequest(
                name="Assets:Broker:Test",
                open_date=date(2020, 1, 1),
                currencies=["not a code"],
            )
