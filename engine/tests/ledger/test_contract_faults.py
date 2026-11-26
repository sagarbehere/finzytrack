"""INV-22 in the write-safety contract's prepare (S0116; moved here from `test_contract.py` in S0156, BL-0022): a fault
injected into each step (an exception, an undecidable load) gives `Refused` naming the step, with nothing on disk
changed, and the step list is pinned to `contract.STEPS` and to the parametrize list. Kept apart from the refusal cases
so that INV-22's Enforced-by line names exactly the fault tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import contract
from ledger_engine.ledger.core import ledger_set
from ledger_engine.ledger.core import semantic_diff as sd
from ledger_engine.ledger.core import source_edit as se
from ledger_engine.ledger.policy import DESKTOP
from tests.ledger import producer as pr
from tests.ledger.test_contract import DINNER, LUNCH, at, baseline, ledger, prep, produced, refused, tree

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]


def _boom(*args: object, **kwargs: object) -> Any:
    msg = "injected"
    raise RuntimeError(msg)


def _patch(target: object, name: str) -> Callable[[pytest.MonkeyPatch], dict[str, Any]]:
    def inject(mp: pytest.MonkeyPatch) -> dict[str, Any]:
        mp.setattr(target, name, _boom)
        return {}

    return inject


def _load_fault(mp: pytest.MonkeyPatch) -> dict[str, Any]:
    return {"load": _boom}


FAULTS: dict[contract.Step, Callable[[pytest.MonkeyPatch], dict[str, Any]]] = {
    "apply": _patch(se, "apply_edits"),
    "parse": _patch(sd, "parse"),
    "diff": _patch(sd, "diff"),
    "spans": _patch(sd, "outside_spans"),
    "load": _load_fault,
    "errors": _patch(ledger_set, "as_before"),
}


def test_every_step_has_a_fault_test() -> None:
    assert set(FAULTS) == set(contract.STEPS) - {"plan"}  # the producer's step is the job's (`preview`)
    mark = test_a_fault_in_any_step_refuses_naming_the_step_and_writes_nothing.pytestmark[0]  # pyright: ignore[reportFunctionMemberAccess]
    assert mark.args[1] == sorted(FAULTS), "the fault test's literal list is not every step"


@pytest.mark.parametrize("step", ["apply", "diff", "errors", "load", "parse", "spans"])
def test_a_fault_in_any_step_refuses_naming_the_step_and_writes_nothing(
    step: contract.Step, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, current = ledger(tmp_path)
    base = baseline(root)
    p = produced(
        current,
        [pr.ReplaceReq(at(LUNCH, root), '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n')],
    )
    before = tree(tmp_path)
    kw = FAULTS[step](monkeypatch)
    out = refused(contract.prepare(current, p, root, base, DESKTOP, **kw), step, "fault")
    assert out.detail == "RuntimeError"
    assert tree(tmp_path) == before


def test_a_load_that_gives_no_decidable_result_refuses(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    before = tree(tmp_path)

    def undecided(root: str, overlay_: Mapping[str, bytes], policy: object) -> Any:
        return None

    refused(prep(root, current, [pr.DeleteReq(at(DINNER, root))], load=undecided), "load", "undecidable")
    assert tree(tmp_path) == before
