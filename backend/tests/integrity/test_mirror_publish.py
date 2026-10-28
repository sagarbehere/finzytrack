"""P1 integrity test for I4: the mirror is published atomically (review:27 §1(d), Appendix B: green in M1).

Spec: ADR-0015 ("Build a new generation file, then publish it atomically. A failed build never publishes.
Generations sit behind a symlink (`current.db`)"); backlog I4; review:27 M1 (e): the swap test with 0 errors,
including an injected failed build.

Interface: `mirror.build_generation(load_result) -> Generation` and `mirror.publish(gen)` (ARCHITECTURE.md §2.3);
`ledger.overlay.load(root, overlay, policy)` (§2.2). Assumed, not decided: `publish` takes the workspace's mirror
directory, and a load result exposes its directives as `.entries`, so a proxy can fail the build part-way
through. Under ADR-0054 the build half of `mirror` and `ledger` live in the engine (`ledger_engine`, built in S0049:
`ledger_engine.ledger.overlay.load` returns `Loaded` with `.entries`, and `policy.DESKTOP` is the desktop policy).
The backend may never import the engine, so the planned names below stay missing here: the mirror-build slice
(I4) moves this file to `engine/tests/` and turns the `planned()` calls into imports; the arrange, act and
assert stay as written.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.integrity.planned import planned

# Two hand-written ledgers: A has 2 transactions (4 postings), B has 3 (6 postings).
LEDGER_A = """option "operating_currency" "USD"
2026-01-01 open Assets:Checking USD
2026-01-01 open Expenses:Food USD
2026-01-05 * "Cafe" "Coffee"
  Expenses:Food    4.50 USD
  Assets:Checking
2026-01-06 * "Grocer" "Bread"
  Expenses:Food    3.20 USD
  Assets:Checking
"""
LEDGER_B = (
    LEDGER_A
    + """2026-01-07 * "Cafe" "Tea"
  Expenses:Food    2.10 USD
  Assets:Checking
"""
)
POSTINGS = {"A": 4, "B": 6}


def _postings(current: Path) -> int:
    with sqlite3.connect(f"file:{current}?mode=ro", uri=True) as db:
        return int(db.execute("SELECT count(*) FROM postings").fetchone()[0])


class _MidBuildFault:
    """A load result whose directives fail after the first three: the build has written rows when it fails."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    @property
    def entries(self) -> Iterator[Any]:
        for i, entry in enumerate(self._real.entries):
            if i == 3:
                raise OSError(28, "mid-build")  # ENOSPC
            yield entry


@pytest.mark.fixme(id="I4", due="M1a", reason="capability not built")
def test_readers_never_see_an_empty_or_partial_mirror(tmp_path: Path) -> None:
    load = planned("ledger_engine.ledger.overlay", "load")
    policy = planned("ledger_engine.ledger.policy", "DESKTOP")
    build_generation = planned("ledger_engine.mirror.publish", "build_generation")
    publish = planned("ledger_engine.mirror.publish", "publish")

    roots: dict[str, Path] = {}
    for name, text in (("A", LEDGER_A), ("B", LEDGER_B)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "main.beancount").write_text(text)
        roots[name] = tmp_path / name / "main.beancount"
    mirror_dir = tmp_path / "mirror"
    publish(build_generation(load(roots["A"], {}, policy)), mirror_dir)
    current = mirror_dir / "current.db"

    seen: list[int] = []
    errors: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                seen.append(_postings(current))
            except sqlite3.Error as exc:  # "no such table" or a torn file is exactly the I4 defect
                errors.append(repr(exc))

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for i in range(200):
            publish(build_generation(load(roots["B" if i % 2 else "A"], {}, policy)), mirror_dir)
        with pytest.raises(OSError, match="mid-build"):  # a fault after some rows were written (verifier F8)
            publish(build_generation(_MidBuildFault(load(roots["A"], {}, policy))), mirror_dir)
    finally:
        stop.set()
        thread.join()

    assert errors == []
    assert seen, "the reader never ran"
    assert set(seen) <= set(POSTINGS.values())
    assert _postings(current) == POSTINGS["B"]  # the failed build published nothing; the last good one stays
