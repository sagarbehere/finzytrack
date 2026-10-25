"""Reach a module that the decided design names but no slice has built yet (S0029, S0032).

The P1 integrity tests (review:27 §1(d)) are written before their capability exists. A static import of a module that
does not exist would break collection, type checking and lint for the whole suite, so each test fetches the planned
symbol here. When the capability lands, the landing slice replaces the call with a normal import and removes the
test's `fixme` marker; the arrange, act and assert stay as written.

A `fixme` test may fail only with `PlannedMissing` (tests/conftest.py; S0032, seeded plants G01-G03): once the planned
module and symbol exist, any failure is a real one, so a buggy first implementation turns the run red instead of
passing as an expected failure. Only the module and the name passed here are checked as "missing": an attribute the
test assumes on the planned object (`LoadPolicy.desktop()`) is checked once its module exists, so a test makes every
`planned()` lookup before it uses any of them (S0032 verifier F2), and a module built by an earlier slice with another
API turns the test red only when every module it needs exists.
"""

from __future__ import annotations

import importlib
from typing import Any


class PlannedMissing(ImportError):
    """The planned module, or the planned name in it, does not exist yet."""

    def __init__(self, module: str, name: str, missing: str | None = None) -> None:
        where = f"module {module} ({missing} is missing)" if missing else f"name {module}.{name}"
        super().__init__(f"planned {where} is not built yet")


def planned(module: str, name: str) -> Any:
    """`module.name`, as named by ARCHITECTURE.md §2.2 and §2.3 or an ADR; PlannedMissing until built.

    Only the planned module itself (or a package on its path) being absent, or the name being absent from it, counts.
    A planned module that exists but fails while importing (a missing dependency, an error in its own code) raises
    that error unchanged.
    """
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (module == missing or module.startswith(missing + ".")):
            raise PlannedMissing(module, name, missing) from exc
        raise
    if not hasattr(mod, name):
        raise PlannedMissing(module, name)
    return getattr(mod, name)
