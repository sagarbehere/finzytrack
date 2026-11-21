"""A copy never writes the bytes of a file the file system encrypts (S0138, BL-0287; owner, 2026-11-18: "a",
choosing "Refuse Copy of an EFS-encrypted ledger, and state the mirror as a limit"; 2026-11-21: "Refuse any file
(Recommended)"). Windows' EFS decrypts a file for its owner as it is read, so a copy would write it in the clear in the
new folder; the copy refuses it before anything is written (`encrypted`, detail `efs`), asking the file-system port
the save's own question (`EntryStat.encrypted`, S0128). Here, on the real disk of every OS, a wrapper around the real
adapter marks one file name as encrypted, so the copy's own port read is held everywhere; `tests/fs/test_windows.py`
encrypts real files with `cipher /e` on the runner."""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem
from ledger_engine.fs.this_os import for_this_os, legacy_locks_for_this_os
from ledger_engine.ledger.copy import CopyPlan, CopyPorts, run
from ledger_engine.ledger.create import Created, NewFile, NotCreated
from tests.windows_pending import COPY_CHECK

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.usefixtures("windows_port")]

WS_JSON = b'{"format": 1, "min_reader": 1, "lineage_id": "7e1c3b52-6a0e-4f43-9d2c-8f1b2c3d4e5f"}\n'
MAIN = (
    'option "title" "Books"\n'
    'option "documents" "docs"\n'
    "2026-01-01 open Assets:Cash EUR\n"
    'include "inc.beancount"\n'
    '2026-01-03 document Assets:Cash "receipt.pdf"\n'
)


class _Dir:
    def __init__(self, inner: DirHandle, names: frozenset[str]) -> None:
        self._inner = inner
        self._names = names

    def stat(self, name: str) -> EntryStat | None:
        st = self._inner.stat(name)
        return dataclasses.replace(st, encrypted=True) if st is not None and name in self._names else st

    def __getattr__(self, attr: str) -> object:
        return getattr(self._inner, attr)

    def __enter__(self) -> _Dir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()


class _EncryptingFS:
    """The real port, with the files of these names reported as encrypted by the file system (as Windows' EFS
    reports them: `FILE_ATTRIBUTE_ENCRYPTED`)."""

    def __init__(self, inner: FileSystem, *names: str) -> None:
        self._inner = inner
        self._names = frozenset(names)

    def open_dir(self, path: str) -> _Dir:
        return _Dir(self._inner.open_dir(path), self._names)

    def __getattr__(self, attr: str) -> object:
        return getattr(self._inner, attr)


@dataclass
class _Job:
    phases: list[str] = field(default_factory=lambda: [])
    gated: int = 0

    def progress(self, phase: str) -> None:
        self.phases.append(phase)

    def gate(self) -> None:
        self.gated += 1


def _ledger(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "docs" / "Assets" / "Cash").mkdir(parents=True)
    (src / "main.beancount").write_text(MAIN)
    (src / "inc.beancount").write_text("2026-01-02 price EUR 1.10 USD\n")
    (src / "receipt.pdf").write_bytes(b"%PDF receipt\n")
    (src / "docs" / "Assets" / "Cash" / "2026-01-04.statement.pdf").write_bytes(b"%PDF statement\n")
    (tmp_path / "app").mkdir()
    return src / "main.beancount"


def _copy(tmp_path: Path, fs: object) -> tuple[object, _Job]:
    root = _ledger(tmp_path)
    plan = CopyPlan(
        "j1", str(tmp_path / "copy"), str(root), (NewFile("workspace.json", WS_JSON),), str(tmp_path / "app"), ()
    )
    job = _Job()
    return run(plan, CopyPorts(fs, legacy_locks_for_this_os()), job), job  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "where"),
    [
        ("main.beancount", "main.beancount"),
        ("inc.beancount", "inc.beancount"),
        ("receipt.pdf", "receipt.pdf"),
        ("2026-01-04.statement.pdf", os.path.join("docs", "Assets", "Cash", "2026-01-04.statement.pdf")),
    ],
)
def test_S0138_a_file_the_file_system_encrypts_is_refused_before_anything_is_written(
    tmp_path: Path, name: str, where: str
) -> None:
    """The root, an include, a `document` entry's file and a file under the documents root. Must not pass: the copy
    made, the target folder made, or the refusal without the file's name."""
    out, job = _copy(tmp_path, _EncryptingFS(for_this_os(), name))
    assert isinstance(out, NotCreated), out
    assert (out.reason, out.detail, out.left) == ("encrypted", "efs", ()), out
    assert out.path is not None
    assert os.path.samefile(out.path, tmp_path / "src" / where)
    assert not (tmp_path / "copy").exists(), "nothing written, not even the target folder"
    assert job.gated == 0


def test_S0138_a_link_to_an_encrypted_file_is_refused(tmp_path: Path) -> None:
    """A ledger file reached through a link: the object it leads to is asked about, as the copy reads it."""
    if sys.platform == "win32":
        pytest.skip("a symbolic link needs a privilege the runner's user may not hold; the fake and macOS hold it")
    root = _ledger(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "secret.beancount").write_text("2026-01-02 price EUR 1.10 USD\n")
    (root.parent / "inc.beancount").unlink()
    (root.parent / "inc.beancount").symlink_to(real / "secret.beancount")
    plan = CopyPlan(
        "j1", str(tmp_path / "copy"), str(root), (NewFile("workspace.json", WS_JSON),), str(tmp_path / "app"), ()
    )
    out = run(plan, CopyPorts(_EncryptingFS(for_this_os(), "secret.beancount"), legacy_locks_for_this_os()), _Job())  # type: ignore[arg-type]
    assert isinstance(out, NotCreated), out
    assert (out.reason, out.detail) == ("encrypted", "efs"), out
    assert not (tmp_path / "copy").exists()


def test_S0138_the_same_ledger_unencrypted_is_copied(tmp_path: Path) -> None:
    """@regression-guard: the wrapper changes nothing else; the port read finds no file encrypted on a plain disk."""
    if sys.platform == "win32":
        pytest.skip(COPY_CHECK)
    out, job = _copy(tmp_path, _EncryptingFS(for_this_os(), "other.beancount"))
    assert out == Created(str(tmp_path / "copy")), out
    assert job.gated == 1
    assert (tmp_path / "copy" / "receipt.pdf").read_bytes() == b"%PDF receipt\n"
