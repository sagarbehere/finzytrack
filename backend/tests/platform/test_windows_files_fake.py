"""The sidecar's Windows identity of a folder over a fake Win32 API, on every OS (S0129 round 1, verifier V3;
`app.platform.adapters.windows_files`; the real calls run on the Windows runner, `test_engine_windows.py`).

Written from the S0128 rule the engine follows (`ledger_engine.fs.windows`): on NTFS and ReFS the 64-bit volume serial
and the 128-bit file ID, the file ID again as `born` (never the creation time, which tunnelling repeats); on FAT32 and
exFAT, which have no `FileIdInfo`, the 32-bit serial, the file index and the creation time; elsewhere no `born`.
@regression-guard (the rule was built in S0129's first commit; these pin it on every OS and kill its loosening
mutants, such as `FS_SERIAL64 = {"ntfs"}`).
"""

from __future__ import annotations

import ctypes
import struct
from typing import Any

import pytest

from tests.platform.fake_win32 import windows_files

SERIAL64, FILE_ID = 0x1122334455667788, (0xAB << 120) | 0x42
SERIAL32, INDEX_HIGH, INDEX_LOW = 0x9ABC_DEF0, 3, 7
CREATED = 133_000_000_000_000_000  # a FILETIME (100 ns since 1601)
FILE_ID_INFO, FILE_BASIC_INFO = 18, 0
ERROR_INVALID_PARAMETER = 87


class FakeFiles:
    """A volume of one file-system kind with one folder (handle 7)."""

    def __init__(self, fs: str) -> None:
        self.fs = fs
        self.error = 0

    def GetInfo(self, h: int, cls: int, buf: Any, size: int) -> int:
        if cls == FILE_ID_INFO:
            if self.fs in ("fat32", "exfat"):
                self.error = ERROR_INVALID_PARAMETER  # no FileIdInfo there (probed in S0126)
                return 0
            raw = struct.pack("<Q", SERIAL64) + FILE_ID.to_bytes(16, "little")
        else:
            raw = struct.pack("<qqqqI", CREATED, 0, 0, 0, 0x10)
        ctypes.memmove(buf, raw, len(raw))
        return 1

    def GetFileInformationByHandle(self, h: int, buf: Any) -> int:
        raw = bytes(28) + struct.pack("<IIIIII", SERIAL32, 0, 0, 1, INDEX_HIGH, INDEX_LOW)
        ctypes.memmove(buf, raw, len(raw))
        return 1

    def GetVolumeInformationByHandleW(self, h: int, *args: Any) -> int:
        name = args[5]
        name.value = self.fs.upper()
        return 1


@pytest.fixture
def errors(monkeypatch: pytest.MonkeyPatch) -> list[FakeFiles]:
    current: list[FakeFiles] = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: current[-1].error if current else 0, raising=False)
    return current


@pytest.mark.parametrize(
    ("fs", "want"),
    [
        ("ntfs", (SERIAL64, FILE_ID, FILE_ID)),
        ("refs", (SERIAL64, FILE_ID, FILE_ID)),
        ("fat32", (SERIAL32, (INDEX_HIGH << 32) | INDEX_LOW, (CREATED - 116_444_736_000_000_000) * 100)),
        ("exfat", (SERIAL32, (INDEX_HIGH << 32) | INDEX_LOW, (CREATED - 116_444_736_000_000_000) * 100)),
        ("udf", (SERIAL64, FILE_ID, None)),
    ],
)
def test_the_identity_follows_the_engines_rule_per_file_system(
    errors: list[FakeFiles], fs: str, want: tuple[int, int, int | None]
) -> None:
    fake = FakeFiles(fs)
    errors.append(fake)
    assert windows_files().identity(fake, 7) == want
