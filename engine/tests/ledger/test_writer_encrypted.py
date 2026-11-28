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
from ledger_engine.ledger.writer import Change, Commit, Landed, NotSaved, Reason, Step, durable_commit, sha256
from tests.fakes.memfs import MemFS
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


def test_S0165_a_link_to_a_gpg_file_reaching_the_writer_is_the_encrypted_refusal() -> None:
    """G1 L1-F6: the save's step 0 refuses a change whose name resolves to a `.gpg` or `.asc` file; when one reaches
    the writer anyway (a link made after step 0), it is refused `encrypted_file` (the sidecar's
    `ENCRYPTED_FILE_NOT_EDITABLE`), never `changed` ("another program changed your ledger"), before any effect."""
    jd, bd = "/w/local/journal", "/w/local/backups"
    fs = MemFS()
    for d in (jd, bd, "/w/L"):
        fs.outside_mkdirs(d)
    fs.outside_write("/w/L/secret.bean.gpg", b"pre\n")
    fs.outside_symlink("/w/L/main.bean", "/w/L/secret.bean.gpg")
    c = Commit("s1", "j7", "k1", "lin1", "build1", (Change("/w/L/main.bean", sha256(b"pre\n"), b"post\n"),), jd, bd)
    before = len(fs.effects)
    out = durable_commit(fs, c)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.open_journal) == (Reason.ENCRYPTED_FILE, False)  # `unchanged`: not re-hashed this early
    assert out.path == "/w/L/secret.bean.gpg"
    assert len(fs.effects) == before
