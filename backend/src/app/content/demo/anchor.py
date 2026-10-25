"""The demo's anchor date comes from an explicit input, never the build clock (ADR-0022; L10-18; INV-07, EP-10).

`SOURCE_DATE_EPOCH` is read as the reproducible-builds specification defines it: whole seconds since
1970-01-01 00:00:00 UTC. Its civil date in UTC is the anchor. This is a build fact (the release tag's date), not
a user's "today", so INV-07's rule against a UTC "today" does not apply to it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

_EPOCH = re.compile(r"[0-9]+")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def anchor_from_epoch(value: str) -> date:
    """The UTC civil date of a `SOURCE_DATE_EPOCH` value (decimal digits only)."""
    if not _EPOCH.fullmatch(value):
        msg = f"SOURCE_DATE_EPOCH must be whole seconds since 1970 in decimal digits, got {value!r}"
        raise ValueError(msg)
    try:
        return datetime.fromtimestamp(int(value), UTC).date()
    except (OverflowError, OSError, ValueError) as e:
        msg = f"SOURCE_DATE_EPOCH is out of range: {value!r}"
        raise ValueError(msg) from e


def anchor_from_iso(value: str) -> date:
    """A strict `YYYY-MM-DD` anchor date."""
    if not _ISO_DATE.fullmatch(value):
        msg = f"the anchor must be a YYYY-MM-DD date, got {value!r}"
        raise ValueError(msg)
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        msg = f"the anchor is not a calendar date: {value!r}"
        raise ValueError(msg) from e
