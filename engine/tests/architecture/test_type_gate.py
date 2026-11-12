"""The engine's type gate cannot be loosened silently (EP-22; S0106, BL-0235, IMP-0064).

`scripts/` was outside pyright's `include`, so the measurement scripts broke unseen from S0087 on. The whole config is
pinned here, as the backend's is (`backend/tests/architecture/test_gates.py`), and no file it checks may lower its
strictness with a file-level comment.
"""

from __future__ import annotations

import json
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2]

# A change here is a change to the gate: a loosening needs the owner (PROCESS.md section 9).
EXPECTED_PYRIGHT: dict[str, object] = {
    "include": ["src", "tests", "scripts"],
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
INCLUDE = ("src", "tests", "scripts")


def test_pyright_config_is_exactly_the_strict_one() -> None:
    assert json.loads((ENGINE / "pyrightconfig.json").read_text()) == EXPECTED_PYRIGHT
    assert EXPECTED_PYRIGHT["include"] == list(INCLUDE)


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
    assert [hit for d in INCLUDE for hit in strictness_comments(ENGINE / d)] == []


def test_strictness_comment_check_sees_a_planted_one(tmp_path: Path) -> None:
    """Must not pass: a planted `# pyright: basic` file goes unseen."""
    (tmp_path / "loose.py").write_text("# pyright: basic\nx = 1\n")
    (tmp_path / "fine.py").write_text("x = 1  # pyright: ignore[reportUnknownVariableType]\n")
    assert strictness_comments(tmp_path) == [str(tmp_path / "loose.py")]
