"""Parsed statements held in memory between the parse, preview and commit calls.

Parsing a CAS takes seconds; corrections re-plan many times. So ``parse`` keeps the
``StatementData`` (never the file, never the password) under a random token for 30
minutes, in process memory only — nothing is written to disk. A restart forgets it
(→ ``STATEMENT_EXPIRED``; the user uploads again). dev-docs/cas-import.md §13.4.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from app.services.statement_import.types import StatementData

TTL_SECONDS = 30 * 60
MAX_ENTRIES = 8

_lock = threading.Lock()
_items: Dict[str, Tuple[float, StatementData]] = {}


def _prune(now: float) -> None:
    for token in [t for t, (at, _) in _items.items() if now - at > TTL_SECONDS]:
        del _items[token]
    while len(_items) > MAX_ENTRIES:
        oldest = min(_items, key=lambda t: _items[t][0])
        del _items[oldest]


def put(stmt: StatementData) -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        now = time.monotonic()
        _items[token] = (now, stmt)
        _prune(now)
    return token


def get(token: str) -> Optional[StatementData]:
    """The statement for ``token`` (and refresh its lifetime), or None if gone."""
    with _lock:
        now = time.monotonic()
        _prune(now)
        item = _items.get(token)
        if item is None:
            return None
        _items[token] = (now, item[1])
        return item[1]


def clear() -> None:
    with _lock:
        _items.clear()
