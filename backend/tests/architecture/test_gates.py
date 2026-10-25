"""The backend's type gate and lint bans cannot be loosened silently (EP-22, INV-07, EP-10, EP-28).

The pyright settings are pinned here; each ruff ban is checked on a planted file, beside a file that must pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
RUFF = Path(sys.executable).parent / "ruff"


def test_pyright_is_strict_with_the_ep22_reports() -> None:
    config = json.loads((BACKEND / "pyrightconfig.json").read_text())
    assert config["typeCheckingMode"] == "strict"
    for report in (
        "reportMissingTypeStubs",
        "reportMatchNotExhaustive",
        "reportImplicitOverride",
        "reportUnnecessaryTypeIgnoreComment",
    ):
        assert config.get(report) == "error", report


def ruff_codes(tmp_path: Path, source: str) -> str:
    target = tmp_path / "src" / "app" / "domain" / "planted.py"
    target.parent.mkdir(parents=True)
    target.write_text(source)
    r = subprocess.run(  # noqa: S603 (fixed argv: the venv's own ruff)
        [str(RUFF), "check", "--config", str(BACKEND / "ruff.toml"), "--output-format", "concise", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout


@pytest.mark.parametrize(
    "source",
    [
        "import datetime\n\nx = datetime.date.today()\n",
        "from datetime import date\n\nx = date.today()\n",
        "from datetime import UTC, datetime\n\nx = datetime.now(UTC).date()\n",  # a UTC "today"
        "import datetime\n\nx = datetime.datetime.utcnow()\n",
        "import time\n\ntime.sleep(1)\n",
        "from time import sleep\n\nsleep(1)\n",
    ],
)
def test_banned_time_apis_are_refused(tmp_path: Path, source: str) -> None:
    assert "TID251" in ruff_codes(tmp_path, source)


def test_blanket_type_ignore_is_refused(tmp_path: Path) -> None:
    # EP-22: every ignore carries a rule code.
    assert "PGH003" in ruff_codes(tmp_path, "x: int = 'a'  # type: ignore\n")


def test_dates_from_an_injected_clock_pass(tmp_path: Path) -> None:
    # Must pass: the allowed shape, a date derived from a value handed in.
    source = "from datetime import date, datetime\n\n\ndef today_from(now: datetime) -> date:\n    return now.date()\n"
    assert ruff_codes(tmp_path, source).strip() in ("", "All checks passed!")
