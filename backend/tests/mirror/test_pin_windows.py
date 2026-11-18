"""The pin on Windows (`app.mirror.files.pin`; `design/specs/mirror/publish.md` §7; S0130, BL-0206): one bounded read
of the pointer file `current.txt`, whose whole content is the generation's name, through a handle that shares deletion
(`app.platform.adapters.windows_files.read_shared`), so the engine's swap (a rename over `current.txt`) is never refused
by a pin's read; a link or a junction at `mirror/` or at `current.txt` is no published mirror. Runs on the Windows
runner (`backend-windows` in `ci.yml`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.windows_pending import WINDOWS_ONLY

if sys.platform != "win32":
    raise pytest.skip.Exception(WINDOWS_ONLY, allow_module_level=True)

import _winapi

from app.mirror import files
from app.platform.adapters import windows_files

pytestmark = [pytest.mark.inv("INV-05")]

GEN = "gen-3-" + "a" * 16 + ".db"
NEXT = "gen-4-" + "b" * 16 + ".db"


def mirror(tmp_path: Path, content: bytes | None = GEN.encode("ascii")) -> Path:
    m = tmp_path / "mirror"
    m.mkdir()
    (m / GEN).write_bytes(b"")
    if content is not None:
        (m / "current.txt").write_bytes(content)
    return m


def test_the_pin_reads_the_pointer(tmp_path: Path) -> None:
    m = mirror(tmp_path)
    assert files.pin(str(m)) == str(m / GEN)


def test_a_current_db_link_is_not_read_on_windows(tmp_path: Path) -> None:
    """On Windows only the pointer names the current generation: a `current.db` there is not looked at."""
    m = mirror(tmp_path, None)
    (m / "current.db").write_bytes(GEN.encode("ascii"))
    out = files.pin(str(m))
    assert isinstance(out, files.MirrorUnavailable)
    assert out.reason == "not_published"


@pytest.mark.parametrize(
    "content",
    [b"", GEN.encode() + b"\r\n", b" " + GEN.encode(), GEN.encode() + b"\0", b"gen-3.db", b"x" * 4096, b"\xff\xfe"],
    ids=["empty", "crlf", "space", "nul", "old-name", "long", "not-ascii"],
)
def test_a_pointer_that_is_not_exactly_a_generation_name_is_not_published(tmp_path: Path, content: bytes) -> None:
    out = files.pin(str(mirror(tmp_path, content)))
    assert isinstance(out, files.MirrorUnavailable)
    assert out.reason == "not_published"


def test_no_pointer_is_not_published(tmp_path: Path) -> None:
    out = files.pin(str(mirror(tmp_path, None)))
    assert isinstance(out, files.MirrorUnavailable)
    assert out.reason == "not_published"


def test_a_junction_at_the_mirror_folder_is_not_followed(tmp_path: Path) -> None:
    real = mirror(tmp_path)
    junction = tmp_path / "other" / "mirror"
    junction.parent.mkdir()
    _winapi.CreateJunction(str(real), str(junction))
    out = files.pin(str(junction))
    assert isinstance(out, files.MirrorUnavailable)
    assert out.reason == "not_published"


def test_a_folder_or_a_junction_at_the_pointer_is_not_read(tmp_path: Path) -> None:
    m = mirror(tmp_path, None)
    (m / "current.txt").mkdir()
    out = files.pin(str(m))
    assert isinstance(out, files.MirrorUnavailable)
    (m / "current.txt").rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(m / "current.txt"))
    out = files.pin(str(m))
    assert isinstance(out, files.MirrorUnavailable)
    assert out.reason == "not_published"


def test_a_swap_during_a_pins_read_is_never_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """publish.md §7 on Windows: the engine renames a new pointer over `current.txt` while a pin may be reading it.
    Here the rename runs inside the pin's own read, while its handle is open: it succeeds, and the pin reads one
    whole name (the old one: its handle still reads the file it opened)."""
    m = mirror(tmp_path)
    (m / NEXT).write_bytes(b"")
    swapped: list[bool] = []
    api_class = windows_files._Api  # pyright: ignore[reportPrivateUsage]

    class Swapping(api_class):
        def __init__(self) -> None:
            super().__init__()
            read = self.ReadFile

            def read_then_swap(*args: Any) -> Any:
                if not swapped:
                    (m / "current.txt.tmp").write_bytes(NEXT.encode("ascii"))
                    os.replace(m / "current.txt.tmp", m / "current.txt")  # MoveFileExW(REPLACE_EXISTING)
                    swapped.append(True)
                return read(*args)

            self.ReadFile = read_then_swap

    monkeypatch.setattr(windows_files, "_Api", Swapping)
    assert files.pin(str(m)) == str(m / GEN)
    assert swapped == [True]
    assert files.pin(str(m)) == str(m / NEXT)
