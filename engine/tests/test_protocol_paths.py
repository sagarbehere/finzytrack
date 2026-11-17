"""The protocol carries each OS's absolute paths unchanged (S0127, BL-0279; ADR-0054): a Windows engine takes
`C:\\...` and `\\\\server\\share\\...` where a POSIX engine takes `/...`, and each refuses the other's (a path of
another OS names no place here). The contract (`contracts/engine-protocol.schema.json`) states both spellings; the
engine checks its own. The POSIX bytes are pinned in `tests/test_posix_path_pins.py`."""

from __future__ import annotations

import re
from typing import cast

import pytest
from pydantic import ValidationError

from ledger_engine.fs import pathmodel
from ledger_engine.protocol import messages as m
from ledger_engine.protocol import schema

WINDOWS_PATHS = ("C:\\Books\\a\\L.beancount", "\\\\nas\\Books\\L.beancount", "\\\\?\\C:\\Books\\a\\L.beancount")
POSIX_PATHS = ("/Books/a/L.beancount", "/")


@pytest.mark.parametrize("path", WINDOWS_PATHS)
def test_a_windows_engine_carries_a_windows_path_unchanged(path: str) -> None:
    with pathmodel.simulating(pathmodel.WINDOWS):
        f = m.JournalFile.model_validate({"path": path, "path_key": path, "created": True})
        assert f.path == path
        assert m.JournalFile.model_validate_json(f.model_dump_json()) == f


@pytest.mark.parametrize("path", [*POSIX_PATHS, "Books\\a", "C:a", "\\a", "\\\\.\\C:\\a"])
def test_a_windows_engine_refuses_what_is_not_a_windows_absolute_path(path: str) -> None:
    with pathmodel.simulating(pathmodel.WINDOWS), pytest.raises(ValidationError):
        m.JournalFile.model_validate({"path": path, "path_key": path, "created": True})


@pytest.mark.parametrize("path", WINDOWS_PATHS)
def test_a_posix_engine_refuses_a_windows_path(path: str) -> None:
    with pathmodel.simulating(pathmodel.POSIX), pytest.raises(ValidationError):
        m.JournalFile.model_validate({"path": path, "path_key": path, "created": True})


def _patterns(node: object) -> set[str]:
    if isinstance(node, dict):
        d = cast("dict[str, object]", node)
        own = {d["pattern"]} if isinstance(d.get("pattern"), str) else set()
        return own.union(*(_patterns(v) for v in d.values()))
    if isinstance(node, list):
        return set().union(*(_patterns(v) for v in cast("list[object]", node)))
    return set()


def test_the_contract_states_both_spellings_of_an_absolute_path() -> None:
    patterns = cast("set[str]", _patterns(schema.build()))
    assert "^/" not in patterns  # the POSIX-only pattern of protocol version 2 before S0127
    absolute = re.compile(m.ABS_PATH_PATTERN)
    assert m.ABS_PATH_PATTERN in patterns
    for p in (*WINDOWS_PATHS, *POSIX_PATHS):
        assert absolute.match(p), p
    for p in ("a", "Books\\a", "C:a", ""):
        assert not absolute.match(p), p
