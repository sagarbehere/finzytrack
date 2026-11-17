"""A swap the volume refuses after all, on macOS (S0125 round 1, verifier F2; the Linux twin is
`test_linux.py::test_a_swap_the_volume_refuses_after_all_is_not_saved_and_changes_nothing`): only the swap is refused,
so the save reaches it (the journal's own exclusive renames still work), rolls back, and says "not saved" with every
file as it was and the record resolved. Adapted from the verifier's repro."""

from __future__ import annotations

import errno
import sys
from pathlib import Path

import pytest

from ledger_engine.fs.this_os import for_this_os
from ledger_engine.ledger.writer import Change, NotSaved, Reason, Step, durable_commit, sha256
from tests.fs.real_volume import commit
from tests.macos_only import MACOS_ONLY
from tests.windows_pending import BL_0280

if sys.platform == "win32":
    raise pytest.skip.Exception(BL_0280, allow_module_level=True)

if sys.platform != "darwin":
    raise pytest.skip.Exception(MACOS_ONLY, allow_module_level=True)

from ledger_engine.fs import darwin  # after the platform guard

pytestmark = pytest.mark.inv("INV-01")


def test_only_the_swap_refused_rolls_back_to_not_saved_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard (the product held at 63b1a240; the test it pins down injected at the journal's rename)."""
    real = darwin.DarwinDir._renamex  # pyright: ignore[reportPrivateUsage]
    swaps: list[str] = []

    def swap_refusing(self: darwin.DarwinDir, a: str, b: str, flags: int) -> None:
        if flags == darwin.RENAME_SWAP:
            swaps.append(b)
            raise OSError(errno.EINVAL, "refused")
        real(self, a, b, flags)

    monkeypatch.setattr(darwin.DarwinDir, "_renamex", swap_refusing)
    main = tmp_path / "L" / "main.bean"
    main.parent.mkdir()
    main.write_bytes(b"pre\n")
    out = durable_commit(for_this_os(), commit(tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert swaps == ["main.bean"], "the swap was reached"
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged) == (Step.REPLACE, Reason.IO_ERROR, True)
    assert main.read_bytes() == b"pre\n"
    assert sorted(p.name for p in main.parent.iterdir()) == ["main.bean"]
    assert sorted(p.name for p in (tmp_path / "local" / "journal").iterdir()) == ["s1-j1.resolved"]
