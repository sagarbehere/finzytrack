"""The G1 refusal tool's runner (S0163; `scripts/g1_runner.py`): each case's edits through the engine's preview and the
confirm's save, in a fresh test copy of the source per case, as the product wires them, with the engine's test producer.
The tool's other half (cases, the table, the report) is `scripts/oracle/g1_refusals.py`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ENGINE_VENV = BACKEND.parent / "engine" / ".venv"

pytestmark = pytest.mark.skipif(not ENGINE_VENV.is_dir(), reason="needs the engine's environment (engine/.venv)")

LEDGER = """option "operating_currency" "USD"
2020-01-01 open Assets:Cash USD
2020-01-01 open Expenses:Food USD
2020-01-02 * "Shop" "lunch"
  Expenses:Food  10.50 USD
  Assets:Cash
2020-02-01 balance Assets:Cash  -10.50 USD
"""

CASES: list[dict[str, object]] = [
    {
        "id": "upd-000",
        "edits": [
            {
                "kind": "replace",
                "path": "main.beancount",
                "line": 4,
                "date": "2020-01-02",
                "keyword": "txn",
                "text": '2020-01-02 * "Shop" "lunch (edited)"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n',
            }
        ],
    },
    {
        "id": "ren-000",
        "edits": [
            {
                "kind": "token",
                "path": "main.beancount",
                "line": 3,
                "date": "2020-01-01",
                "keyword": "open",
                "lineno": 3,
                "old": "Expenses:Food",
                "new": "Expenses:FoodOracle",
            },
            {
                "kind": "token",
                "path": "main.beancount",
                "line": 4,
                "date": "2020-01-02",
                "keyword": "txn",
                "lineno": 5,
                "old": "Expenses:Food",
                "new": "Expenses:FoodOracle",
            },
        ],
    },
    {
        "id": "imp-000",
        "edits": [
            {
                "kind": "insert",
                "default_file": "main.beancount",
                "text": '2020-01-03 * "Shop" "again"\n  Expenses:Food  1.00 USD\n  Assets:Cash\n',
            }
        ],
    },
    {
        "id": "bad-000",
        "edits": [
            {
                "kind": "replace",
                "path": "main.beancount",
                "line": 1,
                "date": "2020-01-02",
                "keyword": "txn",
                "text": '2020-01-02 * "x"\n  Expenses:Food  1 USD\n  Assets:Cash\n',
            }
        ],
    },
]


def run(tmp_path: Path, cases: list[dict[str, object]]) -> tuple[list[dict[str, object]], str]:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.beancount").write_text(LEDGER, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    done = subprocess.run(  # noqa: S603 (fixed argv: a script of this repository)
        [
            sys.executable,
            "-I",
            str(BACKEND / "scripts" / "g1_runner.py"),
            "--source",
            str(src),
            "--root-file",
            "main.beancount",
            "--work",
            str(work),
        ],
        cwd=BACKEND,
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (src / "main.beancount").read_text(encoding="utf-8") == LEDGER  # the source is only read
    assert list(work.iterdir()) == []  # every case's copy removed
    return [json.loads(line) for line in done.stdout.splitlines()], done.stdout


def test_each_case_runs_through_the_preview_and_the_save(tmp_path: Path) -> None:
    """Must not pass: a valid edit not saved, the import's new balance error not a Save anyway, a refused request
    without its contract step and the engine's reason, or a field beyond the fixed ones."""
    answers, out = run(tmp_path, CASES)
    by_id = {a["id"]: a for a in answers}
    assert by_id["upd-000"] == {"id": "upd-000", "outcome": "saved", "code": None, "step": None, "reason": None}
    assert by_id["ren-000"]["outcome"] == "saved", by_id
    assert by_id["imp-000"]["outcome"] == "save_anyway", by_id  # the balance assertion now fails: listed, not refused
    assert by_id["bad-000"] == {
        "id": "bad-000",
        "outcome": "refused",
        "code": "WRITE_REFUSED_CHECK_FAILED",
        "step": "plan",
        "reason": "no_entry_at_position",
    }
    assert "Shop" not in out
    assert str(tmp_path) not in out
