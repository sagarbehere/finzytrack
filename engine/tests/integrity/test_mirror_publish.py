"""P1 integrity test for I4: the mirror is published atomically (review:27 §1(d), Appendix B: green in M1).

Spec: ADR-0015 ("Build a new generation file, then publish it atomically. A failed build never publishes.
Generations sit behind a symlink (`current.db`)"); backlog I4; review:27 M1 (e): the swap test with 0 errors,
including an injected failed build.

Written before the capability in `backend/tests/integrity/` against names the design had then
(`mirror.build_generation`, `mirror.publish(gen)`); moved here by S0088 (BL-0213), since the sidecar may never import
the engine, and pointed at the API S0087 and S0088 built: the open job (`ledger_engine.ledger.opening.run`: the overlay
load, the build into a new generation, its publish by a link swap; `design/specs/mirror/publish.md` §4). The arrange,
act and assert are as written: ledger A and ledger B published alternately while a reader counts postings through
`current.db` as an outside tool does, then a build that fails after some rows were written.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import opening
from ledger_engine.mirror import build as mirror_build
from tests.mirror.publish_support import BUILT_AT, ENGINE_BUILD, KEY, TODAY, fs_now

pytestmark = [
    pytest.mark.inv("INV-05"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's OS parts are macOS-only until BL-0087"),
]

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


class _Job:
    def progress(self, phase: opening.Phase) -> None:
        pass


def _postings(current: Path) -> int:
    with sqlite3.connect(f"file:{current}?mode=ro", uri=True) as db:
        return int(db.execute("SELECT count(*) FROM postings").fetchone()[0])


def _mid_build_fault(real: Any) -> Any:
    """The build's rows, with the postings failing after the first three: the build has written rows when it
    fails."""

    def tables(*args: Any, **kw: Any) -> Any:
        made = real(*args, **kw)

        def postings() -> Iterator[Any]:
            for i, row in enumerate(made["postings"]):
                if i == 3:
                    raise OSError(28, "mid-build")  # ENOSPC
                yield row

        return {**made, "postings": postings()}

    return tables


def test_readers_never_see_an_empty_or_partial_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots: dict[str, Path] = {}
    for name, text in (("A", LEDGER_A), ("B", LEDGER_B)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "main.beancount").write_text(text)
        roots[name] = tmp_path / name / "main.beancount"
    cache = tmp_path / "cache"
    cache.mkdir()

    def publish(name: str) -> opening.Answer:
        target = opening.Target(
            app_cache=str(cache),
            workspace_key=KEY,
            app_local_data=str(tmp_path / "app"),
            lineage="lin1",
            floor=0,
            today=TODAY,
            built_at=BUILT_AT,
            now_ns=fs_now(cache),
            engine_build=ENGINE_BUILD,
        )
        return opening.run(opening.Ports(), str(roots[name]), _Job(), target)

    assert publish("B").outcome == "loaded"  # then A, B, A, ...: another ledger each time
    current = cache / "workspaces" / KEY / "mirror" / "current.db"

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
            assert publish("B" if i % 2 else "A").outcome == "loaded"  # never fresh
        monkeypatch.setattr(mirror_build, "tables", _mid_build_fault(mirror_build.tables))
        with pytest.raises(OSError, match="mid-build"):  # a fault after some rows were written (verifier F8)
            publish("A")
    finally:
        stop.set()
        thread.join()

    assert errors == []
    assert seen, "the reader never ran"
    assert set(seen) <= set(POSTINGS.values())
    assert _postings(current) == POSTINGS["B"]  # the failed build published nothing; the last good one stays
