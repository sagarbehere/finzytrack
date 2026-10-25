"""The backend's type gate and lint bans cannot be loosened silently (EP-22, INV-07, EP-10, EP-28).

The pyright settings are pinned here; each ruff ban is checked on a planted file, beside a file that must pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
RUFF = Path(sys.executable).parent / "ruff"


# EP-22: the whole config is pinned, so no rule, include or ignore can be loosened without changing this test
# (and a loosening needs the owner, PROCESS.md section 9).
EXPECTED_PYRIGHT = {
    "include": ["src", "tests"],
    "typeCheckingMode": "strict",
    "pythonVersion": "3.13",
    "venvPath": ".",
    "venv": ".venv",
    "reportMissingModuleSource": "error",
    "reportMissingTypeStubs": "error",
    "reportMatchNotExhaustive": "error",
    "reportImplicitOverride": "error",
    "reportUnnecessaryTypeIgnoreComment": "error",
}


def test_pyright_config_is_exactly_the_strict_one() -> None:
    assert json.loads((BACKEND / "pyrightconfig.json").read_text()) == EXPECTED_PYRIGHT


def strictness_comments(root: Path) -> list[str]:
    """Files that lower pyright's strictness with a file-level comment (`# pyright: basic` and similar)."""
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for line in path.read_text().splitlines():
            words = line.replace("#", " ").replace(":", " ").replace(",", " ").split()
            if words[:1] == ["pyright"] and {"basic", "standard", "off", "false"} & set(words[1:]):
                hits.append(str(path))
    return hits


def test_no_file_lowers_pyright_strictness() -> None:
    assert strictness_comments(BACKEND / "src") + strictness_comments(BACKEND / "tests") == []


def test_strictness_comment_check_sees_a_planted_one(tmp_path: Path) -> None:
    (tmp_path / "loose.py").write_text("# pyright: basic\nx = 1\n")
    (tmp_path / "fine.py").write_text("x = 1  # pyright: ignore[reportUnknownVariableType]\n")
    assert strictness_comments(tmp_path) == [str(tmp_path / "loose.py")]


def test_ruff_exemptions_are_exactly_the_reviewed_ones() -> None:
    # A per-file ignore can switch a ban off for a whole layer (verifier S0006 R2); each one is listed here.
    config = tomllib.loads((BACKEND / "ruff.toml").read_text())
    assert config["lint"]["per-file-ignores"] == {"**/tests/**": ["S101"]}
    assert "extend-per-file-ignores" not in config["lint"]


def ruff_codes(tmp_path: Path, source: str, layer: str = "domain") -> str:
    target = tmp_path / "src" / "app" / layer / "planted.py"
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


@pytest.mark.parametrize(
    "source",
    [
        "import time\nfrom datetime import UTC, datetime\n\nx = datetime.fromtimestamp(time.time(), UTC).date()\n",
        "import time\n\nx = time.localtime()\n",
        "import time\n\nx = time.strftime('%Y-%m-%d')\n",
    ],
)
def test_system_time_is_refused_outside_domain_too(tmp_path: Path, source: str) -> None:
    # Verifier S0006 R2-F2: layers that may import `time` (api, boot, ...) still get no system clock.
    assert "TID251" in ruff_codes(tmp_path, source, layer="api")


def test_blanket_type_ignore_is_refused(tmp_path: Path) -> None:
    # EP-22: every ignore carries a rule code.
    assert "PGH003" in ruff_codes(tmp_path, "x: int = 'a'  # type: ignore\n")


def test_dates_from_an_injected_clock_pass(tmp_path: Path) -> None:
    # Must pass: the allowed shape, a date derived from a value handed in.
    source = "from datetime import date, datetime\n\n\ndef today_from(now: datetime) -> date:\n    return now.date()\n"
    assert ruff_codes(tmp_path, source).strip() in ("", "All checks passed!")
