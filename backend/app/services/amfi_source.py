"""amfi_source — Indian mutual fund NAVs from AMFI, via mfapi.in (free, keyless).

A holding opts in with ``fetch_symbol: "amfi:<scheme code>"`` (e.g.
``amfi:122639`` for Parag Parikh Flexi Cap Fund - Direct Plan - Growth). Scheme
codes are AMFI's own; https://api.mfapi.in/mf/search?q=<name> lists them with
full plan/option names. Yahoo also carries many Indian funds, but under opaque
Morningstar IDs that don't say which plan or option they are.

Speaks the beanprice source interface the fetcher already uses
(``get_prices_series`` / ``get_latest_price`` returning ``SourcePrice``), so the
fetcher treats it like any other source. NAVs are in INR. mfapi returns a
scheme's whole history in one response (newest first, dates as DD-MM-YYYY).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from beanprice.source import SourcePrice

PREFIX = "amfi:"
_URL = "https://api.mfapi.in/mf/{code}"
_TIMEOUT = 15


def scheme_code(symbol: str) -> Optional[str]:
    """The scheme code in an ``amfi:<code>`` fetch symbol, else None."""
    if not symbol.lower().startswith(PREFIX):
        return None
    return symbol[len(PREFIX):].strip() or None


class AmfiSource:
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()

    def _history(self, code: str) -> list[SourcePrice]:
        resp = self._session.get(_URL.format(code=code), timeout=_TIMEOUT)
        resp.raise_for_status()
        out: list[SourcePrice] = []
        for row in (resp.json() or {}).get("data") or []:
            try:
                when = datetime.strptime(row["date"], "%d-%m-%Y").replace(tzinfo=timezone.utc)
                nav = Decimal(str(row["nav"]))
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue  # one malformed row shouldn't lose the rest
            if nav > 0:
                out.append(SourcePrice(nav, when, "INR"))
        return sorted(out, key=lambda p: p.time)

    def get_prices_series(self, ticker: str, time_begin: datetime, time_end: datetime) -> list[SourcePrice]:
        begin, end = time_begin.date(), time_end.date()
        return [p for p in self._history(ticker) if begin <= p.time.date() <= end]

    def get_latest_price(self, ticker: str) -> Optional[SourcePrice]:
        history = self._history(ticker)
        return history[-1] if history else None
