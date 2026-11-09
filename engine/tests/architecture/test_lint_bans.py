"""The engine's lint bans for time and money cannot be loosened silently (INV-06, INV-07, EP-10, EP-28).

The engine reads no clock (its `today` comes in the request) and keeps money `Decimal`. Each ruff ban of
`engine/ruff.toml` that guards them is checked on a planted file, beside a file that must pass. What ruff cannot see
is `scripts/check_money_dates.py` (its planted cases: `scripts/tests/test_check_money_dates.py`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2]
RUFF = Path(sys.executable).parent / "ruff"
pytestmark = [pytest.mark.inv("INV-06"), pytest.mark.inv("INV-07")]


def ruff_codes(tmp_path: Path, source: str) -> str:
    target = tmp_path / "src" / "ledger_engine" / "mirror" / "planted.py"
    target.parent.mkdir(parents=True)
    target.write_text(source)
    r = subprocess.run(  # noqa: S603 (fixed argv: the venv's own ruff)
        [str(RUFF), "check", "--config", str(ENGINE / "ruff.toml"), "--output-format", "concise", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("from datetime import date\n\nx = date.today()\n", "TID251"),  # the system's "today"
        ("import datetime\n\nx = datetime.date.today()\n", "TID251"),
        ("from datetime import UTC, datetime\n\nx = datetime.now(UTC).date()\n", "TID251"),  # a UTC "today"
        ("from datetime import datetime\n\nx = datetime.utcnow()\n", "TID251"),
        ("import time\n\nx = time.time()\n", "TID251"),
        ("import time\n\nx = time.localtime()\n", "TID251"),
        ("from datetime import datetime\n\nx = datetime(2026, 3, 31, 21, 30)\n", "DTZ001"),  # a naive datetime
        ("from datetime import datetime\n\nx = datetime.fromtimestamp(0)\n", "DTZ006"),
        ("from decimal import Decimal\n\nx = Decimal(0.1)\n", "RUF032"),  # money from a float literal
    ],
)
def test_time_and_float_money_are_refused(tmp_path: Path, source: str, code: str) -> None:
    """@regression-guard (S0092): the bans exist in `engine/ruff.toml`; nothing held them until now."""
    assert code in ruff_codes(tmp_path, source)


def test_dates_and_decimals_from_given_values_pass(tmp_path: Path) -> None:
    """@regression-guard (S0092): the must-pass beside the bans above."""
    # Must pass: the shapes the engine uses, a date from the request and a Decimal from written text.
    source = (
        "from datetime import date\nfrom decimal import Decimal\n\n"
        "today = date.fromisoformat('2026-03-31')\namount = Decimal('10.50')\n"
    )
    assert ruff_codes(tmp_path, source).strip() in ("", "All checks passed!")
