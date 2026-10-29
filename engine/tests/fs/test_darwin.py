"""The file-system port on macOS (`ledger_engine.fs.darwin`; `design/specs/engine-jobs.md` §6.2, §6.3; S0060): the
primitives the durable order relies on, on the real file system (temp folders only, INV-24). Linux and Windows are
BL-0087."""

from __future__ import annotations

import errno
import fcntl
import os
import sys
from pathlib import Path

import pytest

from ledger_engine.fs.darwin import DarwinFS

pytestmark = [
    pytest.mark.inv("INV-01"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the real adapter is macOS-only until BL-0087"),
]


@pytest.fixture
def fs() -> DarwinFS:
    return DarwinFS()


def test_swap_exchanges_two_files_in_one_operation(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    ino_a = (tmp_path / "a").stat().st_ino
    with fs.open_dir(str(tmp_path)) as d:
        d.swap("a", "b")
    assert ((tmp_path / "a").read_bytes(), (tmp_path / "b").read_bytes()) == (b"B", b"A")
    assert (tmp_path / "b").stat().st_ino == ino_a  # the displaced file is the same inode, not a copy


def test_swap_needs_both_names(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    with fs.open_dir(str(tmp_path)) as d, pytest.raises(FileNotFoundError):
        d.swap("a", "missing")


def test_an_exclusive_rename_refuses_an_existing_name_and_leaves_both(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "t").write_bytes(b"T")
    (tmp_path / "x").write_bytes(b"X")
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(OSError, match=r".") as e:
            d.rename_excl("t", "x")
        assert e.value.errno == errno.EEXIST
        d.rename_excl("t", "new")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new", "x"]
    assert (tmp_path / "x").read_bytes() == b"X"


def test_create_is_exclusive_keeps_the_mode_asked_for_and_refuses_a_link(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "taken").write_bytes(b"1")
    (tmp_path / "ln").symlink_to(tmp_path / "elsewhere")
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(FileExistsError):
            d.create("taken", b"2", 0o644)
        with pytest.raises(FileExistsError):
            d.create("ln", b"2", 0o644)  # O_EXCL never follows a link
        d.create("new", b"data", 0o640)
    assert (tmp_path / "taken").read_bytes() == b"1"
    assert not (tmp_path / "elsewhere").exists()
    assert (tmp_path / "new").read_bytes() == b"data"
    assert (tmp_path / "new").stat().st_mode & 0o7777 == 0o640


def test_read_refuses_a_link_and_a_directory_and_answers_none_for_no_entry(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x")
    (tmp_path / "lf").symlink_to(tmp_path / "f")
    (tmp_path / "sub").mkdir()
    with fs.open_dir(str(tmp_path)) as d:
        assert d.read("f") == b"x"
        assert d.read("none") is None
        with pytest.raises(OSError, match=r"."):
            d.read("lf")
        with pytest.raises(OSError, match=r"."):
            d.read("sub")


def test_a_handles_path_follows_its_folder_when_it_moves_and_a_link_put_in_its_place_is_not_followed(
    fs: DarwinFS,
    tmp_path: Path,
) -> None:
    (tmp_path / "L").mkdir()
    (tmp_path / "other").mkdir()
    with fs.open_dir(str(tmp_path / "L")) as d:
        before = d.path()
        (tmp_path / "L").rename(tmp_path / "L-moved")
        (tmp_path / "L").symlink_to(tmp_path / "other")
        assert d.path() == before + "-moved"
        d.create("x", b"1", 0o644)
    assert (tmp_path / "L-moved" / "x").exists()
    assert not (tmp_path / "other" / "x").exists()


def test_open_dir_through_a_link_reports_the_real_folder(fs: DarwinFS, tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "ln").symlink_to(tmp_path / "real")
    with fs.open_dir(str(tmp_path / "ln")) as d:
        assert d.path().endswith("/real")


def test_resolve_gives_the_stored_spelling_on_a_case_insensitive_volume(fs: DarwinFS, tmp_path: Path) -> None:
    if os.pathconf(tmp_path, 11) == 1:
        pytest.skip("the temp volume is case-sensitive")
    (tmp_path / "Ledger").mkdir()
    (tmp_path / "Ledger" / "Main.bean").write_bytes(b"x")
    r = fs.resolve(f"{tmp_path}/LEDGER/main.BEAN")
    assert r.existing[-2:] == ("Ledger", "Main.bean")
    assert r.rest == ()
    assert all(rule.nfd and rule.casefold for rule in r.rules[-2:])


def test_durable_sync_uses_full_fsync_and_falls_back_once_per_volume(
    fs: DarwinFS,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6.2: `F_FULLFSYNC`; where the volume refuses it, `fsync`, reported once per volume."""
    calls: list[int] = []
    real_fcntl = fcntl.fcntl

    def refusing(fd: int, cmd: int, *args: object) -> object:
        if cmd == fcntl.F_FULLFSYNC:
            calls.append(cmd)
            raise OSError(errno.ENOTSUP, "refused")
        return real_fcntl(fd, cmd, *args)  # type: ignore[arg-type]

    notices: list[None] = []
    fallback = DarwinFS(on_fallback=lambda: notices.append(None))
    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", refusing)
    with fallback.open_dir(str(tmp_path)) as d:
        d.create("a", b"1", 0o644)
        d.create("b", b"2", 0o644)
        d.sync()
    assert len(calls) == 3
    assert len(notices) == 1
    monkeypatch.undo()
    seen: list[int] = []

    def recording(fd: int, cmd: int, *args: object) -> object:
        seen.append(cmd)
        return real_fcntl(fd, cmd, *args)  # type: ignore[arg-type]

    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", recording)
    with fs.open_dir(str(tmp_path)) as d:
        d.create("c", b"3", 0o644)
        d.sync()
    assert seen.count(fcntl.F_FULLFSYNC) == 2


def test_another_error_from_full_fsync_is_not_hidden(
    fs: DarwinFS, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(fd: int, cmd: int, *args: object) -> object:
        raise OSError(errno.EIO, "disk")

    monkeypatch.setattr("ledger_engine.fs.darwin.fcntl.fcntl", failing)
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(OSError, match="disk"):
            fs.durable_sync(fd)
    finally:
        os.close(fd)


def test_other_operating_systems_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger_engine.fs import darwin

    monkeypatch.setattr(darwin.sys, "platform", "linux")
    with pytest.raises(darwin.UnsupportedPlatform):
        darwin.for_this_os()
