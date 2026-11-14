"""`route_insert` places each entry where Fava 1.30.16's `find_insert_position` does (S0115; ADR-0009).

The vectors (`fava_insert_vectors.json`) were recorded from Fava 1.30.16 itself on Beancount 3.2.0 by
`scripts/oracle/record_fava_insert.py`, run in a throwaway environment outside the repository (its docstring says
how); they are never edited by hand. Fava's rule: the `insert-entry` options sorted newest first (ties in the load's
order), only options dated before the entry, the entry's accounts in Fava's order (a transaction's postings
reversed), the first option whose pattern matches an account at its start; else an append to the default file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger.core import source_edit as se

pytestmark = pytest.mark.inv("INV-01")

VECTORS: list[dict[str, Any]] = json.loads((Path(__file__).with_name("fava_insert_vectors.json")).read_text("utf-8"))[
    "vectors"
]
CASES = [(v["name"], i, v["files"], ins) for v in VECTORS for i, ins in enumerate(v["inserts"])]


def test_the_vectors_cover_every_rule_of_fava_s_routing() -> None:
    names = {v["name"] for v in VECTORS}
    assert names >= {"no-options", "one-option", "dates", "account-order", "patterns", "directive-accounts"}
    assert names >= {"same-date-ties", "odd-options", "crlf-and-tabs"}
    assert sum(1 for _ in CASES) == 38
    assert any(ins["line"] is None for _, _, _, ins in CASES)
    assert any(ins["line"] is not None for _, _, _, ins in CASES)


@pytest.mark.parametrize(("name", "index", "files", "insert"), CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_route_insert_matches_fava(name: str, index: int, files: list[list[str]], insert: dict[str, Any]) -> None:
    s = se.Snapshot({path: text.encode() for path, text in files})
    placed = se.route_insert(s, insert["text"], files[0][0])
    assert isinstance(placed, se.Insert), (name, index, placed)
    assert (placed.path, placed.line) == (insert["path"], insert["line"]), (name, index)
