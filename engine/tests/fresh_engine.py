"""A load in a test as a fresh engine runs it (engine-jobs §5.1; S0142, BL-0299).

A load records only the modules it imports itself (`ledger_engine.ledger.imports`). Beancount imports some steps of
its load only when a load runs them (`beancount.ops.documents`, `beancount.ops.pad`), so a second load in the same
process would record none of them, as no engine of the product does: every job that loads runs in a fresh engine
it then retires. A test that compares the fingerprints of two loads runs each in `fresh_engine()`, so the result
does not depend on what earlier tests imported."""

from __future__ import annotations

import functools
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager

BEANCOUNT = ("beancount", "beanquery")


@functools.cache
def at_start() -> frozenset[str]:
    """The modules an engine has imported when it starts (`python -m ledger_engine` imports the server), asked of a
    fresh Python once per test run."""
    code = "import sys, ledger_engine.protocol.server\nprint('\\n'.join(sys.modules))"
    argv = [sys.executable, "-I", "-c", code]
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout  # noqa: S603 (this interpreter)
    return frozenset(out.split())


def _beancount(name: str) -> bool:
    return any(name == top or name.startswith(top + ".") for top in BEANCOUNT)


@contextmanager
def fresh_engine() -> Generator[None]:
    """Before the body, the Beancount modules a fresh engine has not imported are set aside, so the body's load
    imports and records them; after it, every module the body imported is dropped, as the engine is retired, and
    the set-aside ones are put back, so the rest of the test process finds its module cache as it was (the fake's
    loads rely on it: `tests/ledger/memledger._warm`)."""
    start = at_start()
    aside = {n: sys.modules.pop(n) for n in [n for n in sys.modules if _beancount(n) and n not in start]}
    before = set(sys.modules)
    try:
        yield
    finally:
        for name in set(sys.modules) - before:
            del sys.modules[name]
        sys.modules.update(aside)
