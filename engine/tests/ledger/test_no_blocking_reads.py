"""No read of a ledger-set file of ours blocks on a FIFO (S0102, BL-0245; S0100 round 2 red team overflow).

A FIFO (or another special file) put where a ledger file was: a blocking `open` of it waits for a writer that never
comes, so the job hangs until the engine is stopped (the save's gate before its commit point: S0100 round 2,
`test_l_target_replaced_by_fifo`; that case is `test_save.py::test_S0102_BL0245_...`). A blocked read would hang its
test, which the gate's hang detector stops (no fixed wait, as `test_fingerprint.py`'s FIFO tests since S0081 r2).
Every read of ours of a file a ledger set names opens without blocking and takes only a regular file, as the copy's
`fingerprint.chunks` does (S0081 r1 F5). The inventory, by behaviour (every `open`, `os.open`, `read_bytes`,
`file_digest` in `engine/src` on a path a ledger set names): the observation (`fingerprint.file_state`: the gate, the
rebuild, the open's freshness, plugin modules), the overlay load's read of each file and its `.asc` encrypted test,
and the rebuild's warnings (`precision.of_disk`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ledger_engine.ledger import fingerprint as fp
from ledger_engine.ledger import overlay, precision
from ledger_engine.ledger.core.ledger_set import Content, Unreadable
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import sha256

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
]


def test_S0102_the_observation_of_a_fifo_is_unreadable_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "main.bean"
    os.mkfifo(fifo)
    assert fp.file_state(str(fifo)) == Unreadable("EINVAL")


def test_S0102_the_observation_of_a_folder_is_unreadable(tmp_path: Path) -> None:
    folder = tmp_path / "main.bean"
    folder.mkdir()
    assert isinstance(fp.file_state(str(folder)), Unreadable)


def test_S0102_the_observation_of_a_regular_file_is_its_bytes(tmp_path: Path) -> None:
    """Must pass: a regular file (through a link too, as the loader follows it) reads as its content."""
    f = tmp_path / "main.bean"
    f.write_bytes(b"x\n")
    link = tmp_path / "link.bean"
    link.symlink_to(f)
    assert fp.file_state(str(f)) == fp.file_state(str(link)) == Content(sha256(b"x\n"))


def test_S0102_the_overlay_load_of_an_include_that_is_a_fifo_never_blocks(tmp_path: Path) -> None:
    main = tmp_path / "main.bean"
    main.write_bytes(b'include "inc.bean"\n')
    os.mkfifo(tmp_path / "inc.bean")
    with pytest.raises(OSError, match="not a regular file"):  # as any unreadable file: the load cannot read it
        overlay.load(str(main), {}, DesktopPolicy())


def test_S0102_the_overlay_loads_encrypted_test_of_an_asc_fifo_never_blocks(tmp_path: Path) -> None:
    """Beancount's `is_encrypted_file` opens a `.asc` file to look for a PGP header: a FIFO there is never opened."""
    main = tmp_path / "main.bean"
    main.write_bytes(b'include "inc.asc"\n')
    os.mkfifo(tmp_path / "inc.asc")
    with pytest.raises(OSError, match="not a regular file"):
        overlay.load(str(main), {}, DesktopPolicy())


def test_S0102_the_rebuilds_warnings_skip_a_fifo_without_blocking(tmp_path: Path) -> None:
    """`precision.of_disk` reads the files a real load parsed again; one that is no regular file now is skipped."""
    fifo = tmp_path / "main.bean"
    os.mkfifo(fifo)
    real = precision.regular

    def stale(p: str) -> bool:  # the file was regular when asked, a FIFO when opened (a swap in between)
        return True if p == str(fifo) else real(p)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(precision, "regular", stale)
        assert precision.of_disk([], {}, [str(fifo)]) == []
