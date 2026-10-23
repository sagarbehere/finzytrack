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

mfapi.in is a free third-party service with no stability promise, so every
failure is raised as an ``AmfiError`` whose message is fit to show the user: the
fetcher puts it in its "could not fetch" list, the search endpoint in its error.
A response that parses but isn't the expected shape is an error too — never an
empty result, which would read as "no such fund".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests
from beanprice.source import SourcePrice

PREFIX = "amfi:"
_URL = "https://api.mfapi.in/mf/{code}"
_SEARCH_URL = "https://api.mfapi.in/mf/search"
_LATEST_URL = "https://api.mfapi.in/mf/{code}/latest"
_TIMEOUT = 15


class AmfiError(Exception):
    """A failure talking to mfapi.in; the message is written for the user."""


class AmfiUnavailable(AmfiError):
    """mfapi.in could not be reached, or answered with an HTTP error."""


class AmfiFormatError(AmfiError):
    """mfapi.in answered, but not in the shape this code expects (it may have changed)."""


class AmfiUnknownScheme(AmfiError):
    """mfapi.in has no scheme with this code."""


_CHANGED = (
    "The Indian mutual fund price service (mfapi.in) answered in an unexpected way. "
    "It may have changed. Enter prices by hand on the Prices tab for now."
)


def scheme_code(symbol: str) -> Optional[str]:
    """The scheme code in an ``amfi:<code>`` fetch symbol, else None."""
    if not symbol.lower().startswith(PREFIX):
        return None
    return symbol[len(PREFIX):].strip() or None


def _parse_row(row: Any) -> Optional[SourcePrice]:
    """One ``{"date": "DD-MM-YYYY", "nav": "…"}`` row as a price, or None if it isn't one.
    A NAV of 0 is a real price: a written-off segregated portfolio ends at 0.00000, and
    is then worth nothing (not its cost)."""
    try:
        when = datetime.strptime(row["date"], "%d-%m-%Y").replace(tzinfo=timezone.utc)
        nav = Decimal(str(row["nav"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return SourcePrice(nav, when, "INR") if nav >= 0 else None


_nav_row = _parse_row


class AmfiSource:
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()

    def _get_json(self, url: str, params: Optional[dict] = None) -> Any:
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise AmfiUnavailable(
                "Could not reach the Indian mutual fund price service (mfapi.in). "
                "Check your internet connection, or try again later."
            ) from e
        if resp.status_code == 404:
            # mfapi answers an unknown *scheme* with 200 and an empty record, so a
            # 404 means the address itself is gone — the service has changed.
            raise AmfiFormatError(_CHANGED)
        if resp.status_code >= 400:
            raise AmfiUnavailable(
                f"The Indian mutual fund price service (mfapi.in) is not working right now "
                f"(error {resp.status_code}). Try again later."
            )
        try:
            return resp.json()
        except ValueError as e:
            raise AmfiFormatError(_CHANGED) from e

    def _scheme(self, url: str, code: str, params: Optional[dict] = None) -> list:
        """The ``data`` rows of a scheme record; raises if the scheme is unknown."""
        body = self._get_json(url, params)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise AmfiFormatError(_CHANGED)
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        if not body["data"] and not meta.get("scheme_name"):
            raise AmfiUnknownScheme(f"There is no Indian mutual fund with scheme code {code}. Check the code.")
        return body["data"]

    def _history(self, code: str, begin: date, end: date) -> list[SourcePrice]:
        """NAVs from ``begin`` to ``end``. mfapi filters by date itself (``startDate`` /
        ``endDate``), so a fetch that fills the last few days downloads those days, not
        the scheme's whole history; a scheme with no NAV in the range still answers
        with its name, so an empty range is never mistaken for an unknown scheme."""
        rows = self._scheme(_URL.format(code=code), code,
                            {"startDate": begin.isoformat(), "endDate": end.isoformat()})
        out = [p for p in (_parse_row(r) for r in rows) if p is not None]
        if rows and not out:
            raise AmfiFormatError(_CHANGED)  # rows we can't read at all: the format changed
        return sorted(out, key=lambda p: p.time)

    def get_prices_series(self, ticker: str, time_begin: datetime, time_end: datetime) -> list[SourcePrice]:
        begin, end = time_begin.date(), time_end.date()
        # Filter here too: the result stays right if the service ever ignores the range.
        return [p for p in self._history(ticker, begin, end) if begin <= p.time.date() <= end]

    def get_latest_price(self, ticker: str) -> Optional[SourcePrice]:
        return self.latest_nav(ticker)

    # ── Finding a fund's scheme code (Setup & Metadata → Price symbol → Find) ──

    def search(self, query: str) -> list[tuple[str, str]]:
        """``[(scheme_code, scheme_name)]`` for schemes whose name matches."""
        body = self._get_json(_SEARCH_URL, {"q": query})
        if not isinstance(body, list):
            raise AmfiFormatError(_CHANGED)
        out = [
            (str(h["schemeCode"]), str(h["schemeName"]))
            for h in body
            if isinstance(h, dict) and h.get("schemeCode") and h.get("schemeName")
        ]
        if body and not out:
            raise AmfiFormatError(_CHANGED)  # matches we can't read at all: the format changed
        return out

    def latest_nav(self, code: str) -> Optional[SourcePrice]:
        """The scheme's latest NAV — one small request, unlike the full history."""
        rows = self._scheme(_LATEST_URL.format(code=code), code)
        return _nav_row(rows[0]) if rows else None
