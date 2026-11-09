"""Civil dates (INV-07; ADR-0023; EP-23): the one home of "today" on the main sidecar.

A date the user sees or the ledger holds is a civil date in the user's time zone, never the UTC date: at 21:30 on
31 March in Los Angeles it is already 04:30 on 1 April in UTC, and an entry dated by UTC would land on the wrong day.
"Today" is `civil_date(clock.now())`: the injected clock's instant (EP-10) in the zone the app runs in, which on the
desktop is the user's. A caller that reads the clock once for both a date and an instant passes the same `now` to
both. `scripts/check_money_dates.py` refuses `.date()` and `.astimezone()` without a zone everywhere else.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo


def civil_date(instant: datetime, tz: tzinfo | None = None) -> date:
    """The civil date of `instant` in `tz`; without `tz`, in the zone the app runs in (the machine's; INV-07).

    A wall time without a UTC offset is refused: it is not an instant, and reading it in some zone would guess.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        msg = f"a civil date needs an instant with a UTC offset, not {instant.isoformat()}"
        raise ValueError(msg)
    return instant.astimezone(tz).date()
