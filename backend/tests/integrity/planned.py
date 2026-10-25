"""Reach a module that the decided design names but no slice has built yet (S0029).

The P1 integrity tests (review:27 §1(d)) are written before their capability exists. A static import of a module that
does not exist would break collection, type checking and lint for the whole suite, so each test fetches the planned
symbol here. When the capability lands, the landing slice replaces the call with a normal import and removes the
test's `fixme` marker; the arrange, act and assert stay as written.
"""

from __future__ import annotations

import importlib
from typing import Any


def planned(module: str, name: str) -> Any:
    """`module.name`, as named by ARCHITECTURE.md §2.2 and §2.3 or an ADR; ImportError until built."""
    return getattr(importlib.import_module(module), name)
