"""The protocol carries each OS's absolute paths unchanged (S0127, BL-0279; ADR-0054): a Windows engine takes
`C:\\...` and `\\\\server\\share\\...` where a POSIX engine takes `/...`, and each refuses the other's (a path of
another OS names no place here). The contract (`contracts/engine-protocol.schema.json`) states both spellings
(`messages.ABS_PATH_PATTERN`); each engine validates its own OS's (`messages.HOST_ABS_PATH_PATTERN`), so on macOS and
Linux validation is exactly as before (`tests/test_posix_path_pins.py`). On Windows the engine-windows CI job runs the
host cases."""

from __future__ import annotations

import re
import sys
from typing import cast

import pytest
from pydantic import ValidationError

from ledger_engine.fs import pathmodel
from ledger_engine.protocol import messages as m
from ledger_engine.protocol import schema

WINDOWS_PATHS = ("C:\\Books\\a\\L.beancount", "C:/Books/L.beancount", "\\\\nas\\Books\\L.beancount", "\\\\?\\C:\\a\\L")
POSIX_PATHS = ("/Books/a/L.beancount", "/")
NEITHER = ("a", "Books\\a", "C:a", "\\a", "")
WINDOWS = sys.platform == "win32"
WINDOWS_PATTERN = r"^(?:[A-Za-z]:[\\/]|[\\/]{2}(?:[^.\\/]|[.][^\\/]))"


def _validate(path: str) -> m.JournalFile:
    return m.JournalFile.model_validate({"path": path, "path_key": path, "created": True})


@pytest.mark.parametrize("path", WINDOWS_PATHS if WINDOWS else POSIX_PATHS)
def test_this_engine_carries_its_own_oss_paths_unchanged(path: str) -> None:
    f = _validate(path)
    assert f.path == path
    assert m.JournalFile.model_validate_json(f.model_dump_json()) == f


@pytest.mark.parametrize("path", [*(POSIX_PATHS if WINDOWS else WINDOWS_PATHS), *NEITHER])
def test_this_engine_refuses_another_oss_paths_and_relative_ones(path: str) -> None:
    with pytest.raises(ValidationError):
        _validate(path)


def test_the_engine_validates_by_its_own_oss_pattern() -> None:
    assert (WINDOWS_PATTERN if WINDOWS else "^/") == m.HOST_ABS_PATH_PATTERN


@pytest.mark.parametrize(
    ("path", "absolute"),
    [*((p, True) for p in WINDOWS_PATHS), *((p, False) for p in (*POSIX_PATHS, *NEITHER)), ("\\\\.\\C:\\a", False)],
)
def test_the_windows_pattern_agrees_with_the_windows_path_model(path: str, absolute: bool) -> None:
    assert bool(re.match(WINDOWS_PATTERN, path)) is absolute
    assert pathmodel.WINDOWS.is_absolute(path) is absolute


def _patterns(node: object) -> set[str]:
    """Every `pattern` string in a JSON schema."""
    found: set[str] = set()
    if isinstance(node, dict):
        d = cast("dict[str, object]", node)
        pattern = d.get("pattern")
        if isinstance(pattern, str):
            found.add(pattern)
        for v in d.values():
            found |= _patterns(v)
    elif isinstance(node, list):
        for v in cast("list[object]", node):
            found |= _patterns(v)
    return found


def test_the_contract_states_both_spellings_of_an_absolute_path() -> None:
    patterns = _patterns(schema.build())
    assert "^/" not in patterns  # the POSIX-only pattern before S0127
    assert m.ABS_PATH_PATTERN in patterns
    absolute = re.compile(m.ABS_PATH_PATTERN)
    for p in (*WINDOWS_PATHS, *POSIX_PATHS):
        assert absolute.match(p), p
    for p in NEITHER:
        assert not absolute.match(p), p
