"""The `balance` domain vectors through the mirror's `balance_assertions` rows (spec design/specs/kernel/vectors.md,
family `balance`, consumer "the `balance_assertions` rows"; surface.toml `balance_assertions`; ADR-0024; INV-02).

Each vector is a ledger, its assertion and Beancount 3.2.0's answer: the tolerance it allows, the difference (actual
minus asserted) and its errors. The vectors' own check (`scripts/oracle/tests/test_domain_vectors.py`) holds that
the tolerance and difference explain Beancount's verdict: the assertion fails exactly when |difference| exceeds the
tolerance. The row for the assertion must say what the spec says: `tolerance` is the one written on the directive
(the vector's input, NULL when none), `passed` is Beancount's verdict, and `diff_number`/`diff_currency` are
Beancount's difference when it failed and NULL when it passed. Every vector, built from disk.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from .support import build_text, one

pytestmark = pytest.mark.inv("INV-02")

VECTORS = Path(__file__).resolve().parents[3] / "corpora" / "vectors" / "vectors.json"
BALANCE: list[dict[str, Any]] = json.loads(VECTORS.read_text(encoding="utf-8"))["families"]["balance"]
ASSERTION_DATE = "2020-06-01"  # the generator's date for the item under test


def expected(vector: dict[str, Any]) -> tuple[Any, ...]:
    inp, out = vector["input"], vector["output"]
    failed = abs(Decimal(out["difference"])) > Decimal(out["tolerance"])
    currency = inp["amount"].split()[1]
    return (
        inp["tolerance"],
        0 if failed else 1,
        out["difference"] if failed else None,
        currency if failed else None,
    )


def test_every_balance_vector(tmp_path_factory: pytest.TempPathFactory) -> None:
    failures: list[str] = []
    verdicts: set[int] = set()
    for vector in BALANCE:
        con = build_text(tmp_path_factory.mktemp("vector"), vector["input"]["ledger"])
        have = one(
            con,
            "SELECT tolerance, passed, diff_number, diff_currency FROM balance_assertions"
            " WHERE account = ? AND date = ?",
            vector["input"]["account"],
            ASSERTION_DATE,
        )
        want = expected(vector)
        verdicts.add(have[1])
        if have != want:
            failures.append(f"{vector['id']}: {have} != {want}")
    assert failures == []
    assert len(BALANCE) == 10
    assert verdicts == {0, 1}  # both verdicts are shown, so neither can be hard-wired
