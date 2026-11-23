"""A file the file system encrypts is never saved (S0128 round 1; owner, 2026-11-17: "1a"): the writer refuses it
before its first effect, `encrypted_file`, unchanged, with no backup made, so its bytes never reach a backup or a
staging file in the clear. Windows' EFS sets `EntryStat.encrypted` (`tests/fs/test_windows.py` encrypts a real file
with `cipher /e`); here a wrapper around the real adapter marks one, so the rule is held on every OS."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from ledger_engine.fs.port import DirHandle, EntryStat, FileSystem
from ledger_engine.fs.this_os import for_this_os
from ledger_engine.ledger.writer import Change, Landed, NotSaved, Reason, Step, durable_commit, sha256
from tests.fs.real_volume import commit

pytestmark = pytest.mark.inv("INV-01")


class _Dir:
    def __init__(self, inner: DirHandle, name: str) -> None:
        self._inner = inner
        self._name = name

    def stat(self, name: str) -> EntryStat | None:
        st = self._inner.stat(name)
        return dataclasses.replace(st, encrypted=True) if st is not None and name == self._name else st

    def __getattr__(self, attr: str) -> object:
        return getattr(self._inner, attr)

    def __enter__(self) -> _Dir:
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()


class _EncryptingFS:
    """The real port, with one file name reported as encrypted by the file system."""

    def __init__(self, inner: FileSystem, name: str) -> None:
        self._inner = inner
        self._name = name

    def resolve(self, path: str) -> object:
        return self._inner.resolve(path)

    def same_path(self, now: str, recorded: str) -> bool:
        return self._inner.same_path(now, recorded)

    def open_dir(self, path: str) -> _Dir:
        return _Dir(self._inner.open_dir(path), self._name)


def test_an_encrypted_file_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    c = commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n"))
    out = durable_commit(_EncryptingFS(for_this_os(), "main.bean"), c)  # type: ignore[arg-type]
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged, out.open_journal) == (Step.BACKUP, Reason.ENCRYPTED_FILE, True, False)
    assert out.path is not None
    assert os.path.samefile(out.path, main)
    assert main.read_bytes() == b"pre\n"
    assert os.listdir(tmp_path / "app" / "local" / "backups") == [], "a backup of the encrypted file in the clear"
    assert os.listdir(tmp_path / "app" / "local" / "journal") == []
    assert sorted(os.listdir(led)) == ["main.bean"]


def test_the_same_file_not_encrypted_is_saved(tmp_path: Path) -> None:
    """@regression-guard: the wrapper changes nothing else."""
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    c = commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n"))
    out = durable_commit(_EncryptingFS(for_this_os(), "other.bean"), c)  # type: ignore[arg-type]
    assert isinstance(out, Landed), out
    assert main.read_bytes() == b"post\n"
