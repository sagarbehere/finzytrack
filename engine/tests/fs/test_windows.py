"""The file-system port on Windows (`ledger_engine.fs.windows`, reached through `ledger_engine.fs.this_os` inside
`windows_port_under_test`; S0128, BL-0280, BL-0218; ADR-0069; `design/specs/engine-jobs.md` §6.2, §6.3, §6.8, §15):
the primitives the durable order relies on and the writer's save, crash and recovery, on the runner's real disk (temp
folders only, INV-24) and on the scratch volumes the manual CI run formats (`FT_TEST_VOLUMES`, `;`-separated: FAT32,
exFAT and ReFS; `ci.yml`, the `engine-windows` job).

The ownership rule (owner, 2026-11-16: "Rule unchanged (Recommended)"): the Windows port writes exactly what the macOS
port writes; where Windows cannot give a primitive safely it takes the stated refusal or the stated best-effort mode
(ADR-0069: §6.8's mode on every Windows volume). The product does not select this adapter yet (BL-0087): the first test
holds that.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
import warnings
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs import this_os
from ledger_engine.fs.port import (
    SERIAL_NAMESPACE,
    Caps,
    FileSystem,
    FoldRule,
    Kind,
    NotOffered,
    Right,
    folder_id,
    same_folder,
)
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.writer import Change, Landed, NotSaved, Reason, Step, durable_commit, sha256
from tests.fs.real_volume import commit, real_volume_run

WINDOWS_ONLY = "the Windows adapter's own tests (macOS: tests/fs/test_darwin.py; Linux: tests/fs/test_linux.py)"

if sys.platform != "win32":
    raise pytest.skip.Exception(WINDOWS_ONLY, allow_module_level=True)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.usefixtures("windows_port")]

UPPER = FoldRule(nfd=False, casefold=False, upper=True)
EXACT = FoldRule(nfd=False, casefold=False)
DEADLINE = 15.0


@pytest.fixture
def fs() -> FileSystem:
    return this_os.for_this_os()


def _volumes() -> list[str]:
    return [v for v in os.environ.get("FT_TEST_VOLUMES", "").split(";") if v]


def _kind_of(fs: FileSystem, path: Path) -> str:
    with fs.open_dir(str(path)) as d:
        return d.mark().fs_type


# -- the product does not select it ------------------------------------------------------------------------------------

NO_SELECTION = """
import json, sys
import ledger_engine.protocol.server
from ledger_engine.fs import this_os
from ledger_engine.protocol.server import Engine
e = Engine()
replies = [json.loads(e.handle(json.dumps({"v": 2, "id": i, "op": op, "params": {}}).encode() + b"\\n"))
           for i, op in ((1, "handshake"), (2, "ping"))]
try:
    this_os.for_this_os()
    port = "selected"
except this_os.UnsupportedPlatform:
    port = "refused"
try:
    this_os.legacy_locks_for_this_os()
    locks = "selected"
except this_os.UnsupportedPlatform:
    locks = "refused"
print(json.dumps({"ok": [r["ok"] for r in replies], "port": port, "locks": locks, "supported": this_os.supported(),
                  "loaded": "ledger_engine.fs.windows" in sys.modules}))
"""


def test_the_product_on_windows_still_refuses_file_operations_and_never_loads_the_adapter() -> None:
    """Must not pass if the product selects the Windows adapter (BL-0087 turns writes on): a fresh
    engine process answers its handshake and ping, refuses the port and the legacy locks, says the platform is not
    supported, and has not imported `ledger_engine.fs.windows`."""
    run = subprocess.run(  # noqa: S603 (our own interpreter and code)
        [sys.executable, "-c", NO_SELECTION], capture_output=True, text=True, check=False, timeout=120
    )
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.splitlines()[-1])
    assert got == {"ok": [True, True], "port": "refused", "locks": "refused", "supported": False, "loaded": False}


def test_outside_the_test_switch_this_process_refuses_too() -> None:
    """The switch is a context variable: a thread started here runs in a fresh context, where it is off."""
    assert this_os.supported()
    seen: list[object] = []

    def ask() -> None:
        seen.append(this_os.supported())
        try:
            this_os.for_this_os()
        except this_os.UnsupportedPlatform as e:
            seen.append(e)

    t = threading.Thread(target=ask)
    t.start()
    t.join(DEADLINE)
    assert seen[0] is False
    assert isinstance(seen[1], this_os.UnsupportedPlatform)


# -- the primitives ----------------------------------------------------------------------------------------------------


def test_every_windows_volume_offers_the_exclusive_rename_and_no_swap(fs: FileSystem, tmp_path: Path) -> None:
    """ADR-0069: Windows has no exchange of two names, so the writer takes §6.8's best-effort mode everywhere."""
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    with fs.open_dir(str(tmp_path)) as d:
        assert d.caps() == Caps(swap=False, excl=True, links=False)
        with pytest.raises(NotOffered):
            d.swap("a", "b")
        with pytest.raises(FileExistsError):
            d.rename_excl("a", "b")
        assert ((tmp_path / "a").read_bytes(), (tmp_path / "b").read_bytes()) == (b"A", b"B"), "nothing done"
        d.rename_excl("a", "c")
        d.rename("c", "b")  # the plain rename replaces
    assert sorted(os.listdir(tmp_path)) == ["b"]
    assert (tmp_path / "b").read_bytes() == b"A"


def test_create_is_exclusive_streams_its_chunks_and_refuses_a_link(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "x").write_bytes(b"old")
    os.symlink(tmp_path / "x", tmp_path / "l")
    with fs.open_dir(str(tmp_path)) as d:
        for name in ("x", "l"):
            with pytest.raises(FileExistsError):
                d.create(name, b"new", 0o644)
        ident = d.create("y", iter([b"one ", b"two ", b"three"]), 0o600)
        st = d.stat("y")
        assert st is not None
        assert st.identity == ident
        assert (st.kind, st.nlink, st.locked) == (Kind.FILE, 1, False)
    assert (tmp_path / "x").read_bytes() == b"old"
    assert (tmp_path / "y").read_bytes() == b"one two three"


def test_read_refuses_a_link_and_a_folder_answers_none_for_no_entry_and_reads_at_most_its_limit(
    fs: FileSystem, tmp_path: Path
) -> None:
    (tmp_path / "f").write_bytes(b"0123456789")
    (tmp_path / "dir").mkdir()
    os.symlink(tmp_path / "f", tmp_path / "flink")
    os.symlink(tmp_path / "dir", tmp_path / "dlink", target_is_directory=True)
    with fs.open_dir(str(tmp_path)) as d:
        assert d.read("f") == b"0123456789"
        assert d.read("f", 4) == b"0123"
        assert d.read("none") is None
        assert d.sha256("f", 4) == (sha256(b"0123"), 4)
        for name in ("dir", "flink", "dlink"):
            with pytest.raises(OSError, match=r"."):
                d.read(name)
        kinds = {n: s.kind for n in ("f", "dir", "flink", "dlink") if (s := d.stat(n)) is not None}
        assert kinds == {"f": Kind.FILE, "dir": Kind.DIR, "flink": Kind.LINK, "dlink": Kind.LINK}
        got = d.read_identified("f")
        assert got is not None
        assert got[1] == d.sync_file("f") == d.stat("f").identity  # type: ignore[union-attr]


def test_entry_operations_relative_to_the_folder_handle(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"f")
    os.symlink(tmp_path, tmp_path / "self", target_is_directory=True)
    with fs.open_dir(str(tmp_path)) as d:
        d.mkdir("sub")
        with pytest.raises(FileExistsError):
            d.mkdir("sub")
        with d.open_child("sub") as s:
            s.create("inner", b"i", 0o644)
            assert s.names() == ["inner"]
            assert s.path() == str(tmp_path / "sub")
        with pytest.raises(OSError, match=r".") as no_link:
            d.open_child("self")
        assert no_link.value.errno == errno.ELOOP, "a link is never followed below an open folder"
        with pytest.raises(OSError, match=r".") as not_dir:
            d.open_child("f")
        assert not_dir.value.errno == errno.ENOTDIR
        with pytest.raises(OSError, match=r".") as full:
            d.rmdir("sub")
        assert full.value.errno == errno.ENOTEMPTY
        with pytest.raises(OSError, match=r".") as link_dir:
            d.rmdir("self")
        assert link_dir.value.errno == errno.ENOTDIR
        before = d.stat("f")
        d.touch("f")
        after = d.stat("f")
        assert before is not None
        assert after is not None
        assert after.mtime_ns >= before.mtime_ns
        d.remove("self")  # a link to a folder is removed as an entry, never followed
        assert (tmp_path / "sub").is_dir()
        with pytest.raises(OSError, match=r".") as symlink:
            d.symlink("f", "lnk")
        assert symlink.value.errno == errno.ENOTSUP, "the mirror's pointer file (S0130) makes no link"
        assert d.readlink("none") is None
        with pytest.raises(OSError, match=r"."):
            d.readlink("f")
        d.remove("f")
        assert "f" not in d.names()
        with d.open_child("sub") as s:
            s.remove("inner")
        d.rmdir("sub")
        d.sync()
        assert d.names() == []


def test_a_handles_path_follows_its_folder_when_it_moves(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with fs.open_dir(str(tmp_path / "a")) as d:
        assert d.path() == str(tmp_path / "a")
        os.rename(tmp_path / "a", tmp_path / "b")
        assert d.path() == str(tmp_path / "b")
        assert not fs.same_path(d.path(), str(tmp_path / "a"))


def test_resolve_gives_the_stored_names_through_any_spelling_of_the_path(fs: FileSystem, tmp_path: Path) -> None:
    """Q1 (BL-0280): one place has one canonical path and one key, whatever spelling opened it: another case, the
    long-path prefix, `/`, a link, an 8.3 short name and a volume GUID path of a lettered drive."""
    (tmp_path / "Books").mkdir()
    (tmp_path / "Books" / "Main.bean").write_bytes(b"m")
    want = fs.resolve(str(tmp_path / "Books" / "Main.bean"))
    assert want.rest == ()
    assert want.existing[-2:] == ("Books", "Main.bean")
    os.symlink(tmp_path / "Books", tmp_path / "lnk", target_is_directory=True)
    spellings = [
        str(tmp_path / "BOOKS" / "main.BEAN"),
        "\\\\?\\" + str(tmp_path / "Books" / "Main.bean"),
        str(tmp_path / "Books" / "Main.bean").replace("\\", "/"),
        str(tmp_path / "lnk" / "Main.bean"),
        _short(tmp_path / "Books" / "Main.bean"),
        _volume_guid_path(tmp_path / "Books" / "Main.bean"),
    ]
    for spelling in spellings:
        if spelling is None:
            continue
        got = fs.resolve(spelling)
        assert (got.existing, got.rest, got.identity) == (want.existing, want.rest, want.identity), spelling
        assert core_paths.path_key(got) == core_paths.path_key(want), spelling
    missing = fs.resolve(str(tmp_path / "BOOKS" / "New" / "x.bean"))
    assert missing.existing[-1] == "Books"
    assert missing.rest == ("New", "x.bean")
    assert missing.rules[0] == UPPER, "a root is keyed without case (a drive letter, a server name)"


def _short(path: Path) -> str | None:
    """The 8.3 short spelling of `path` (None where the volume makes no short names)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(32768)
    n = k32.GetShortPathNameW(str(path), buf, 32768)
    return buf.value if n and buf.value != str(path) else None


def _volume_guid_path(path: Path) -> str | None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    root = ctypes.create_unicode_buffer(1024)
    guid = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(str(path), root, 1024):
        return None
    if not k32.GetVolumeNameForVolumeMountPointW(root.value, guid, 1024):
        return None
    return guid.value + str(path)[len(root.value) :]


def test_a_subst_drive_resolves_to_the_drive_it_names(fs: FileSystem, tmp_path: Path) -> None:
    """Q1: a `subst` drive letter is a spelling of another folder, never a second root of it."""
    letter = next((c for c in "PQRSTUVWXYZ" if not Path(f"{c}:\\").exists()), None)
    if letter is None:
        pytest.skip("no free drive letter on this runner")
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "f.bean").write_bytes(b"x")
    subprocess.run(["subst", f"{letter}:", str(tmp_path / "s")], check=True)  # noqa: S603, S607
    try:
        got = fs.resolve(f"{letter}:\\f.bean")
        want = fs.resolve(str(tmp_path / "s" / "f.bean"))
        assert got.existing == want.existing
        assert core_paths.path_key(got) == core_paths.path_key(want)
    finally:
        subprocess.run(["subst", f"{letter}:", "/d"], check=False)  # noqa: S603, S607


def test_a_network_spelling_of_a_local_folder_has_no_drive_id_so_the_save_refuses_it(
    fs: FileSystem, tmp_path: Path
) -> None:
    """Q1: `\\\\localhost\\C$\\...` is another root for the same folder (Windows gives the share's path back), so a
    network share has no drive ID on Windows (as on macOS, BL-0219): the save refuses it before writing, and its two
    spellings never hold one journal. A server name in any case has one key."""
    drive = str(tmp_path)[0]
    unc = f"\\\\localhost\\{drive}$" + str(tmp_path)[2:]
    try:
        os.stat(unc)
    except OSError:
        pytest.skip("the runner's administrative share is not reachable")
    (tmp_path / "main.bean").write_bytes(b"pre\n")
    a = fs.resolve(unc + "\\main.bean")
    b = fs.resolve(f"\\\\LOCALHOST\\{drive.lower()}$" + str(tmp_path)[2:] + "\\main.bean")
    assert core_paths.path_key(a) == core_paths.path_key(b)
    with fs.open_dir(unc) as d:
        assert d.volume_id() is None
    out = durable_commit(fs, commit(tmp_path / "app", Change(unc + "\\main.bean", sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.NO_DRIVE_ID
    assert (tmp_path / "main.bean").read_bytes() == b"pre\n"


def test_names_compare_without_case_on_the_runners_disk(fs: FileSystem, tmp_path: Path) -> None:
    with fs.open_dir(str(tmp_path)) as d:
        assert d.rule() == UPPER
        d.create("Case.bean", b"c", 0o644)
        with pytest.raises(FileExistsError):
            d.create("CASE.BEAN", b"x", 0o644)
        assert d.names() == ["Case.bean"]


def test_an_ntfs_folder_with_its_case_sensitive_flag_compares_exactly(fs: FileSystem, tmp_path: Path) -> None:
    folder = tmp_path / "cs"
    folder.mkdir()
    fsutil = ["fsutil.exe", "file", "setCaseSensitiveInfo", str(folder), "enable"]
    run = subprocess.run(  # noqa: S603 (the system's own tool, no shell)
        fsutil,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        pytest.skip(f"the case-sensitive flag cannot be set on this runner: {run.stdout.strip()} {run.stderr.strip()}")
    with fs.open_dir(str(folder)) as d:
        assert d.rule() == EXACT


def test_a_hard_linked_file_is_refused_before_anything_is_written(fs: FileSystem, tmp_path: Path) -> None:
    led = tmp_path / "led"
    led.mkdir()
    (led / "main.bean").write_bytes(b"pre\n")
    os.link(led / "main.bean", tmp_path / "other")
    with fs.open_dir(str(led)) as d:
        st = d.stat("main.bean")
        assert st is not None
        assert st.nlink == 2
    out = durable_commit(fs, commit(tmp_path / "app", Change(str(led / "main.bean"), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged) == (Step.BACKUP, Reason.HARD_LINKED, True)
    assert (led / "main.bean").read_bytes() == b"pre\n"


def test_a_read_only_file_is_locked_and_refused_before_anything_is_written(fs: FileSystem, tmp_path: Path) -> None:
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    os.chmod(main, 0o444)  # the read-only attribute
    try:
        out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved), out
        assert (out.step, out.reason, out.unchanged) == (Step.BACKUP, Reason.LOCKED, True)
        assert main.read_bytes() == b"pre\n"
    finally:
        os.chmod(main, 0o644)  # the read-only attribute cleared


def test_a_folder_with_an_inheritable_deny_entry_refuses_the_save(fs: FileSystem, tmp_path: Path) -> None:
    """The ACL rule as on macOS (`handles.blocking_entry`): any deny entry blocks, whoever it names, unless it is not
    inherited and denies only what protects the folder itself."""
    led = tmp_path / "led"
    led.mkdir()
    (led / "main.bean").write_bytes(b"pre\n")
    with fs.open_dir(str(led)) as d:
        assert not [e for e in d.acl() if e.deny]
    subprocess.run(["icacls", str(led), "/deny", "*S-1-1-0:(OI)(DE)"], check=True, capture_output=True)  # noqa: S603, S607
    try:
        with fs.open_dir(str(led)) as d:
            denies = [e for e in d.acl() if e.deny]
        assert denies, "the deny entry is read"
        assert "delete" in denies[0].perms
        assert "file_inherit" in denies[0].flags
        out = durable_commit(fs, commit(tmp_path / "app", Change(str(led / "main.bean"), sha256(b"pre\n"), b"p\n")))
        assert isinstance(out, NotSaved), out
        assert (out.reason, out.unchanged) == (Reason.PERMISSION, True)
        assert out.detail is not None
        assert "delete" in out.detail
    finally:
        subprocess.run(["icacls", str(led), "/remove:d", "*S-1-1-0"], check=False, capture_output=True)  # noqa: S603, S607
    assert (led / "main.bean").read_bytes() == b"pre\n"


# -- the open-file refusal ---------------------------------------------------------------------------------------------


def test_a_file_another_program_holds_open_is_not_saved_unchanged_and_named(fs: FileSystem, tmp_path: Path) -> None:
    """An editor's usual open shares reading and writing, not deletion: Windows cannot rename over the file then
    (probed). The save stops before its first write, "not saved", unchanged, naming the file."""
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    with main.open("rb"):  # Python's open shares reading and writing, never deletion
        with fs.open_dir(str(led)) as d, pytest.raises(OSError, match=r".") as busy:
            d.allowed("main.bean", Right.DELETE)
        assert busy.value.errno == errno.EBUSY
        out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged, out.path) == (Step.BACKUP, Reason.IO_ERROR, True, str(main))
    assert main.read_bytes() == b"pre\n"
    assert not [n for n in os.listdir(led) if ".ft-" in n], "nothing of ours left beside it"


def test_a_file_opened_after_the_check_stops_the_replace_and_the_save_rolls_back_unchanged(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The editor opens the file between the save's checks and its rename: the rename fails with nothing changed, the
    rollback undoes the save's other file from its backup, and the answer is "not saved", unchanged."""
    from ledger_engine.fs import windows

    led = tmp_path / "led"
    led.mkdir()
    a, b = led / "a.bean", led / "b.bean"
    a.write_bytes(b"a\n")
    b.write_bytes(b"b\n")
    holder: list[Any] = []
    real = windows.WindowsDir.rename

    def rename(self: windows.WindowsDir, src: str, dst: str) -> None:
        if dst != "b.bean" or holder:
            real(self, src, dst)
            return
        holder.append(b.open("rb"))  # opened without sharing deletion, just before the replace...
        try:
            real(self, src, dst)
        finally:
            holder.pop().close()  # ...and closed after it failed, so the rollback can undo a.bean

    monkeypatch.setattr(windows.WindowsDir, "rename", rename)
    c = commit(tmp_path / "app", Change(str(a), sha256(b"a\n"), b"A\n"), Change(str(b), sha256(b"b\n"), b"B\n"))
    out = durable_commit(fs, c)
    assert isinstance(out, NotSaved), out
    assert (out.step, out.unchanged) == (Step.REPLACE, True), out
    assert (a.read_bytes(), b.read_bytes()) == (b"a\n", b"b\n")
    assert not [n for n in os.listdir(led) if ".ft-" in n]


# -- identity (BL-0218, Windows) ---------------------------------------------------------------------------------------


def test_the_replaces_last_check_catches_a_file_removed_and_made_again_with_the_same_bytes(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the save's resolve and its replace another program removes the file and makes it again with the same
    bytes: on NTFS and ReFS the file ID changes (probed), so `writer._check_replaced` stops the save, unchanged; the
    creation time would not have caught it (tunnelling)."""
    from ledger_engine.fs import windows

    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    assert _kind_of(fs, led) in ("ntfs", "refs")
    real = windows.WindowsDir.rename_excl
    done: list[bool] = []

    def rename_excl(self: windows.WindowsDir, src: str, dst: str) -> None:
        real(self, src, dst)
        if dst.endswith(".open") and not done:  # the journal is open: the replace comes next
            done.append(True)
            main.unlink()
            main.write_bytes(b"pre\n")

    monkeypatch.setattr(windows.WindowsDir, "rename_excl", rename_excl)
    out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert done
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged) == (Step.REPLACE, Reason.CHANGED, True)
    assert main.read_bytes() == b"pre\n"


def test_a_folder_made_again_at_once_is_another_folder_by_its_file_id_not_its_creation_time(
    fs: FileSystem, tmp_path: Path
) -> None:
    p = tmp_path / "again"
    p.mkdir()
    with fs.open_dir(str(p)) as d:
        first, mark1 = folder_id(d), d.mark()
        created1 = os.stat(p).st_birthtime_ns
    p.rmdir()
    p.mkdir()
    with fs.open_dir(str(p)) as d:
        assert not same_folder(d, first), "a folder made again at the same path is another folder"
        mark2 = d.mark()
        created2 = os.stat(p).st_birthtime_ns
    assert mark1.fs_type == "ntfs"
    assert mark1.volume == mark2.volume
    assert mark1.born != mark2.born, "born is the file ID on NTFS"
    print(f"tunnelled creation time: {created1 == created2}")  # noqa: T201 (recorded in the journal)


def test_mark_and_drive_id_on_the_runners_disk(fs: FileSystem, tmp_path: Path) -> None:
    with fs.open_dir(str(tmp_path)) as d:
        mark = d.mark()
        dev, ino = d.identity()
    assert mark.fs_type == "ntfs"
    assert mark.volume == str(uuid.uuid5(SERIAL_NAMESPACE, f"{dev:016X}")), "the 64-bit serial as a name-based UUID"
    assert mark.born == ino
    assert os.stat(tmp_path).st_dev == dev, "Python's st_dev is FileIdInfo's 64-bit serial"
    assert os.stat(tmp_path).st_ino == ino, "Python's st_ino is FileIdInfo's 128-bit file ID"


# -- the save, its crash and its recovery ------------------------------------------------------------------------------


def test_a_save_its_crash_and_its_recovery_on_the_runners_disk(fs: FileSystem, tmp_path: Path) -> None:
    """§6.8's best-effort mode run in full (the macOS disk-image run's checks, `real_volume.py`)."""
    ledger = tmp_path / "led"
    (ledger / "inc").mkdir(parents=True)
    real_volume_run(fs, tmp_path, ledger)


def test_a_landed_save_writes_exactly_the_new_bytes_and_leaves_no_name_of_ours(fs: FileSystem, tmp_path: Path) -> None:
    led = tmp_path / "led"
    led.mkdir()
    (led / "main.bean").write_bytes(b"pre\n")
    out = durable_commit(
        fs,
        commit(
            tmp_path / "app",
            Change(str(led / "main.bean"), sha256(b"pre\n"), b"post\n"),
            Change(str(led / "y2026" / "new.bean"), None, b"new\n"),
        ),
    )
    assert isinstance(out, Landed), out
    assert (led / "main.bean").read_bytes() == b"post\n"
    assert (led / "y2026" / "new.bean").read_bytes() == b"new\n"
    assert sorted(os.listdir(led)) == ["main.bean", "y2026"]
    record = out.record
    assert {f.fs_type for f in record.folders} == {"ntfs"}


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_a_save_its_crash_and_its_recovery_on_a_real_scratch_volume(
    fs: FileSystem, tmp_path: Path, volume: str
) -> None:
    """FAT32, exFAT and ReFS: the best-effort mode in full, with each kind's drive ID and folder identity."""
    with fs.open_dir(volume) as v:
        mark = v.mark()
        caps = v.caps()
        dev, ino = v.identity()
    assert caps == Caps(swap=False, excl=True, links=False)
    assert mark.fs_type in ("fat32", "exfat", "refs"), mark
    assert mark.volume is not None
    if mark.fs_type == "refs":
        assert mark.born == ino
    else:
        assert mark.born == 0, "a FAT or exFAT drive's top level keeps no creation time (probed)"
    print(f"{volume}: {mark.fs_type} serial {dev:x} file id {ino:x}")  # noqa: T201 (recorded in the journal)
    ledger = Path(volume) / f"ft-{uuid.uuid4().hex}"
    (ledger / "inc").mkdir(parents=True)
    try:
        real_volume_run(fs, tmp_path, ledger)
    finally:
        shutil.rmtree(ledger, ignore_errors=True)


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_a_ledger_at_a_fat_drives_top_level_is_refused_no_folder_id(
    fs: FileSystem, tmp_path: Path, volume: str
) -> None:
    if _kind_of(fs, Path(volume)) == "refs":
        pytest.skip("ReFS keeps a file ID for its top level")
    main = Path(volume) / f"ft-{uuid.uuid4().hex}.bean"
    main.write_bytes(b"pre\n")
    try:
        out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, NotSaved), out
        assert (out.reason, out.unchanged) == (Reason.NO_FOLDER_ID, True)
        assert main.read_bytes() == b"pre\n"
    finally:
        main.unlink()


# -- the locks ---------------------------------------------------------------------------------------------------------


def _held_by_another(path: Path) -> bool:
    """Whether another handle holds a lock on the file: `LockFileEx` tried without waiting from this process."""
    import msvcrt

    from ledger_engine.fs import windows

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock = k32.LockFileEx
    lock.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    lock.restype = ctypes.c_int
    fd = os.open(path, os.O_RDWR)
    try:
        flags = windows.LOCKFILE_EXCLUSIVE_LOCK | windows.LOCKFILE_FAIL_IMMEDIATELY
        return not lock(msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(windows.OVERLAPPED()))
    finally:
        os.close(fd)  # closing releases a lock this process took


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(DEADLINE)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def _engine(*extra: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _ask(proc: subprocess.Popen[bytes], op: str, rid: int) -> dict[str, Any]:
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps({"v": 2, "id": rid, "op": op, "params": {}}).encode() + b"\n")
    proc.stdin.flush()
    got: list[bytes] = []
    reader = threading.Thread(target=lambda: got.append(proc.stdout.readline()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    reader.join(DEADLINE)
    assert got, f"no answer to {op}"
    return json.loads(got[0])


def _app_id(fs: FileSystem, folder: Path) -> str:
    with fs.open_dir(str(folder)) as d:
        return ":".join(str(n) for n in folder_id(d))


def test_the_engine_lock_is_held_after_the_handshake_until_the_engine_ends(
    fs: FileSystem, tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """§5.1 on Windows: `LockFileEx` over the whole file, made with `locks/` through the app folder's handle; the lock
    file is never deleted."""
    lock = tmp_path / "locks" / "engine.lock"
    proc = _engine("--engine-lock", str(lock), "--app-folder-id", _app_id(fs, tmp_path))
    procs.append(proc)
    assert _ask(proc, "handshake", 1)["ok"]
    assert _ask(proc, "ping", 2)["ok"]
    assert _held_by_another(lock), "the engine answered after its handshake without holding the lock"
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait(DEADLINE)
    assert lock.is_file()
    assert not _held_by_another(lock)


def test_an_engine_waits_for_the_lock_an_earlier_engine_holds(
    fs: FileSystem, tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    lock = tmp_path / "locks" / "engine.lock"
    first = _engine("--engine-lock", str(lock), "--app-folder-id", _app_id(fs, tmp_path))
    procs.append(first)
    assert _ask(first, "handshake", 1)["ok"]
    assert _ask(first, "ping", 2)["ok"]
    second = _engine("--engine-lock", str(lock), "--app-folder-id", _app_id(fs, tmp_path))
    procs.append(second)
    assert _ask(second, "handshake", 1)["ok"], "the handshake is answered: the lock is taken after it"
    assert second.stdin is not None
    assert second.stdout is not None
    second.stdin.write(json.dumps({"v": 2, "id": 2, "op": "ping", "params": {}}).encode() + b"\n")
    second.stdin.flush()
    got: list[bytes] = []
    reader = threading.Thread(target=lambda: got.append(second.stdout.readline()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    reader.join(0.5)
    assert not got, "a request was answered while an earlier engine held the lock"
    assert first.stdin is not None
    first.stdin.close()
    first.wait(DEADLINE)
    reader.join(DEADLINE)
    assert got, "the request was not answered once the lock was free"


def test_the_engine_lock_in_a_replaced_app_folder_is_refused_and_makes_nothing(fs: FileSystem, tmp_path: Path) -> None:
    from ledger_engine.protocol.app_folder import parse_identity
    from ledger_engine.protocol.server import AppFolderChangedError, hold_engine_lock

    app = tmp_path / "app"
    app.mkdir()
    start = parse_identity(_app_id(fs, app))
    app.rmdir()
    app.mkdir()
    with pytest.raises(AppFolderChangedError):
        hold_engine_lock(str(app / "locks" / "engine.lock"), start)
    assert os.listdir(app) == [], "nothing made in another folder"


# -- the C: oddity of S0126 (a create refused right after a replace) ---------------------------------------------------


@pytest.mark.parametrize("where", ["tmp", "volumes"])
def test_a_create_at_the_staging_name_right_after_a_replace_is_never_refused(
    fs: FileSystem, tmp_path: Path, where: str
) -> None:
    """S0126's probe saw `FILE_EXISTS` at a create right after `ReplaceFileW` moved that name away, on C: only. The
    adapter's replace is a rename by handle; 300 rounds of create-staging, rename-over, create-staging again, on the
    runner's temp folder and on each scratch volume, must never meet the old name."""
    places = [tmp_path] if where == "tmp" else [Path(v) / f"ft-{uuid.uuid4().hex}" for v in _volumes()]
    if not places:
        pytest.skip("no scratch volumes given (FT_TEST_VOLUMES)")
    for place in places:
        place.mkdir(exist_ok=True)
        try:
            with fs.open_dir(str(place)) as d:
                d.create("x.bean", b"0", 0o644)
                refused: list[str] = []
                for i in range(300):
                    try:
                        d.create(".x.bean.ft-s-j.new", str(i).encode(), 0o644)
                    except OSError as e:
                        refused.append(f"{i}: {e}")
                        with contextlib.suppress(OSError):
                            d.remove(".x.bean.ft-s-j.new")
                        continue
                    d.rename(".x.bean.ft-s-j.new", "x.bean")
                    d.sync()
                assert not refused, (str(place), refused[:5], len(refused))
        finally:
            if where == "volumes":
                shutil.rmtree(place, ignore_errors=True)


# -- Windows' upper case against the path model's (S0127's upper-case limit) ----------------------------------


def test_the_path_models_upper_case_never_splits_two_names_windows_takes_as_one() -> None:
    """`pathmodel.ordinal_upper` against Windows' own table (`RtlUpcaseUnicodeChar`, the table a volume this Windows
    formats carries) over every 16-bit character: where Windows maps a character to another, the model must map it to
    the same one (else one file could get two keys: a split, INV-32). Where the model upper-cases more, two names merge
    into one key (fails closed); those are counted, not refused."""
    from ledger_engine.fs.pathmodel import ordinal_upper

    nt = ctypes.WinDLL("ntdll")
    up = nt.RtlUpcaseUnicodeChar
    up.argtypes = [ctypes.c_uint16]
    up.restype = ctypes.c_uint16
    splits: list[str] = []
    merges = 0
    for code in range(0x10000):
        if 0xD800 <= code <= 0xDFFF:
            continue  # a lone surrogate is no character; NTFS compares it as itself
        c = chr(code)
        windows = chr(up(code))
        model = ordinal_upper(c)
        if model == windows:
            continue
        if windows != c:
            splits.append(f"U+{code:04X}: Windows {ord(windows):04X}, model {ord(model[0]):04X}")
        else:
            merges += 1
    print(f"the model upper-cases {merges} characters Windows leaves as they are (merges, fail closed)")  # noqa: T201
    assert not splits, splits[:20]


# -- round 1 (S0128: the verifier and the data-safety red team) --------------------------------------------------------


def _watch_name(target: Path, stop: threading.Event, seen: dict[str, int]) -> None:
    """Ask for `target` until `stop`, counting the looks that found it missing: by `GetFileAttributesW`, which opens
    no handle (as S0126's probe asked). Python's `os.stat` opens the file sharing nothing, which makes a rename over it
    fail at that instant (the 2026-11-17 run 11): not a missing name, a refused rename."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    attrs = k32.GetFileAttributesW
    attrs.argtypes = [ctypes.c_wchar_p]
    attrs.restype = ctypes.c_uint32
    name = str(target)
    while not stop.is_set():
        seen["looks"] += 1
        if attrs(name) == 0xFFFFFFFF:
            seen["missing"] += 1


@contextlib.contextmanager
def _held_reading_only(path: Path) -> Generator[None]:
    """Another program holds `path` open sharing reading only, as Word and Excel hold a document."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = k32.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    h = create(str(path), 0x80000000, 0x1, None, 3, 0x80, None)  # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
    assert h not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
    try:
        yield
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def test_RT_F1_a_rollback_while_another_program_holds_the_file_reading_only_is_not_saved_and_pauses_nothing(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red team F1: Word or Excel opens the document (sharing reading only) between the save's checks and its rename,
    and keeps it open through the rollback. The rename fails with nothing changed; the rollback puts a.bean back and
    needs no write-open of b.bean, which it never replaced, so the answer is "not saved", unchanged, nothing paused."""
    from ledger_engine.fs import windows

    led = tmp_path / "led"
    led.mkdir()
    a, b = led / "a.bean", led / "b.bean"
    a.write_bytes(b"a\n")
    b.write_bytes(b"b\n")
    stack = contextlib.ExitStack()
    held: list[bool] = []
    real = windows.WindowsDir.rename

    def rename(self: windows.WindowsDir, src: str, dst: str) -> None:
        if dst == "b.bean" and not held:
            held.append(True)
            stack.enter_context(_held_reading_only(b))  # opened just before the replace, held to the end
        real(self, src, dst)

    monkeypatch.setattr(windows.WindowsDir, "rename", rename)
    with stack:
        c = commit(tmp_path / "app", Change(str(a), sha256(b"a\n"), b"A\n"), Change(str(b), sha256(b"b\n"), b"B\n"))
        out = durable_commit(fs, c)
        assert isinstance(out, NotSaved), out
        assert (out.step, out.unchanged, out.open_journal) == (Step.REPLACE, True, False), out
        assert (a.read_bytes(), b.read_bytes()) == (b"a\n", b"b\n")
    assert not [n for n in os.listdir(led) if ".ft-" in n]


def _icacls(path: Path) -> list[str]:
    """The entries `icacls` lists for `path`, without the path itself."""
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout  # noqa: S603, S607
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("Successfully")]
    return [lines[0][len(str(path)) :].strip(), *lines[1:]]


def test_owner_1a_a_replaced_file_keeps_its_own_acl_hidden_flag_and_streams(fs: FileSystem, tmp_path: Path) -> None:
    """Owner, 2026-11-17: "1a": a ledger file made private with its own ACL (inheritance off, the user alone) stays
    private after a save, hidden stays hidden, and an alternate data stream comes along byte for byte."""
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    user = os.environ["USERNAME"]
    private = ["icacls", str(main), "/inheritance:r", "/grant:r", f"{user}:(F)", "/grant:r", "*S-1-5-32-545:(R)"]
    subprocess.run(private, check=True, capture_output=True)  # noqa: S603 (the system's own tool)
    subprocess.run(["attrib", "+h", str(main)], check=True, capture_output=True)  # noqa: S603, S607
    (led / "main.bean:note").write_bytes(b"a stream\n")
    before = _icacls(main)
    assert not [e for e in before if "(I)" in e], before  # its own ACL: nothing inherited from the folder
    assert any("Users:(R)" in e for e in before), before
    out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, Landed), out
    assert main.read_bytes() == b"post\n"
    assert _icacls(main) == before, "the file's own ACL was dropped"
    assert os.stat(main).st_file_attributes & 0x2, "the hidden attribute was dropped"
    assert (led / "main.bean:note").read_bytes() == b"a stream\n"


def test_owner_1a_an_efs_encrypted_file_is_refused_before_anything_is_written(fs: FileSystem, tmp_path: Path) -> None:
    """Owner, 2026-11-17: "1a": a file Windows' EFS encrypts is never saved, so its bytes never reach a backup or a
    staging file in the clear: `encrypted_file`, unchanged, no backup made."""
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    run = subprocess.run(["cipher", "/e", str(main)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if run.returncode != 0 or not os.stat(main).st_file_attributes & 0x4000:
        pytest.skip(f"EFS is not available on this runner: {run.stdout.strip()} {run.stderr.strip()}")
    out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.unchanged, out.path) == (Step.BACKUP, Reason.ENCRYPTED_FILE, True, str(main))
    assert main.read_bytes() == b"pre\n"
    assert not list((tmp_path / "app" / "local" / "backups").iterdir()), "a backup in the clear"
    assert not [n for n in os.listdir(led) if ".ft-" in n]


# -- S0135 (BL-0285): the carry-over's tests (V2-F3) and the volume's own flags (V2-Q-B) ------------------------------

PERSISTENT_ACLS = 0x8  # FILE_PERSISTENT_ACLS (winnt.h): the volume keeps ACLs
NAMED_STREAMS = 0x40000  # FILE_NAMED_STREAMS: the volume keeps alternate data streams
NOT_SUPPORTED, INVALID_PARAMETER = 50, 87  # the two answers the base took as "this volume keeps none"


def _ledger(tmp_path: Path) -> tuple[Path, Path]:
    led = tmp_path / "led"
    led.mkdir()
    main = led / "main.bean"
    main.write_bytes(b"pre\n")
    return led, main


def _save(fs: FileSystem, tmp_path: Path, *changes: Change) -> Landed | NotSaved:
    out = durable_commit(fs, commit(tmp_path / "app", *changes))
    assert isinstance(out, Landed | NotSaved), out
    return out


def _windows_fs(fs: FileSystem) -> Any:
    from ledger_engine.fs import windows

    assert isinstance(fs, windows.WindowsFS)
    return fs


def _security_calls(
    fs: FileSystem, monkeypatch: pytest.MonkeyPatch, refuse: Callable[[str, bool], int | None] | None = None
) -> list[tuple[str, bool, str]]:
    """Every `GetSecurityInfo` and `FileStreamInfo` the adapter asks from now on, as (call, on a folder, the volume's
    file-system name); `refuse(call, on_folder)` gives an error code to answer instead of asking Windows, or None.
    A folder's DACL is read through a handle `WindowsDir._reopen` gives (`READ_CONTROL` only, which cannot be asked
    its attributes), so those handles are noted as they are made, with their folder's file system, until closed; a
    file's through a handle opened for reading, which is asked its volume."""
    from ledger_engine.fs import windows

    api = _windows_fs(fs).api
    calls: list[tuple[str, bool, str]] = []
    folders: dict[int, str] = {}
    real_sec, real_info = api.GetSecurityInfo, api.GetInfo
    real_reopen, real_close = windows.WindowsDir._reopen, windows._Handle.close  # pyright: ignore[reportPrivateUsage]

    def reopen(self: windows.WindowsDir, access: int) -> windows._Handle:  # pyright: ignore[reportPrivateUsage]
        h = real_reopen(self, access)
        folders[h.handle] = self.fs_type()
        return h

    def close(self: windows._Handle) -> None:  # pyright: ignore[reportPrivateUsage]
        if self.h is not None:
            folders.pop(self.h, None)
        real_close(self)

    def kind_of(h: int) -> tuple[bool, str]:
        if h in folders:
            return True, folders[h]
        return False, windows._Handle(api, h).volume()[0]  # pyright: ignore[reportPrivateUsage] (not closed)

    def security(h: int, *rest: object) -> int:
        folder, kind = kind_of(h)
        calls.append(("GetSecurityInfo", folder, kind))
        code = refuse("GetSecurityInfo", folder) if refuse else None
        return code if code is not None else real_sec(h, *rest)

    def info(h: int, cls: int, buf: object, size: int) -> int:
        if cls != 7:  # FileStreamInfo only
            return real_info(h, cls, buf, size)
        folder, kind = kind_of(h)
        calls.append(("FileStreamInfo", folder, kind))
        code = refuse("FileStreamInfo", folder) if refuse else None
        if code is None:
            return real_info(h, cls, buf, size)
        ctypes.set_last_error(code)
        return 0

    monkeypatch.setattr(windows.WindowsDir, "_reopen", reopen)
    monkeypatch.setattr(windows._Handle, "close", close)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(api, "GetSecurityInfo", security)
    monkeypatch.setattr(api, "GetInfo", info)
    return calls


def _refusing(which: str, *, folder: bool, code: int) -> Callable[[str, bool], int | None]:
    """A `refuse` for `_security_calls`: `code` for the call `which` on a folder (or on a file), else Windows' own."""

    def refuse(call: str, on_folder: bool) -> int | None:
        return code if call == which and on_folder is folder else None

    return refuse


def _unchanged(out: Landed | NotSaved, led: Path, main: Path) -> None:
    assert isinstance(out, NotSaved), out
    assert out.unchanged, out
    assert main.read_bytes() == b"pre\n"
    assert not [n for n in os.listdir(led) if ".ft-" in n]


def test_V2_F3_a_file_that_inherits_its_folders_acl_and_has_an_entry_of_its_own_keeps_both(
    fs: FileSystem, tmp_path: Path
) -> None:
    """@regression-guard (it holds on the base; S0128 round 2, V2-F3): rule 1a keeps the DACL's inherited state, so
    a file with an explicit entry beside the entries it inherits keeps exactly that listing. A carry-over that always
    marks the DACL protected turns the inherited entries into its own (`icacls` drops their `(I)`)."""
    _, main = _ledger(tmp_path)
    subprocess.run(["icacls", str(main), "/grant", "*S-1-5-32-545:(R)"], check=True, capture_output=True)  # noqa: S603, S607
    before = _icacls(main)
    assert [e for e in before if "(I)" in e], before  # inherited entries
    assert any("Users:(R)" in e and "(I)" not in e for e in before), before  # and one of its own
    out = _save(fs, tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    assert isinstance(out, Landed), out
    assert _icacls(main) == before


@pytest.mark.parametrize("archive", [True, False])
def test_V2_F3_a_saved_file_has_the_archive_attribute_whatever_it_had_before_on_every_volume(
    fs: FileSystem, tmp_path: Path, archive: bool
) -> None:
    """@regression-guard (the 2026-11-20 run 5 saw it hold on the base on NTFS; V2-F3): the archive attribute means
    "changed since the last backup", so a saved file has it set, as Windows sets it on any changed file, whether the
    old file had it set or cleared (owner, 2026-11-20: "Drop archive from rule 1a (Recommended)"; rule 1a keeps only
    the hidden attribute). A carry-over of a cleared archive attribute would hide the save from a backup tool. On the
    runner's disk and on each scratch volume."""
    places = [tmp_path / "led", *[Path(v) / f"ft-{uuid.uuid4().hex}" for v in _volumes()]]
    for i, place in enumerate(places):
        place.mkdir(exist_ok=True)
        try:
            main = place / "main.bean"
            main.write_bytes(b"pre\n")
            subprocess.run(["attrib", "+a" if archive else "-a", str(main)], check=True, capture_output=True)  # noqa: S603, S607
            assert bool(os.stat(main).st_file_attributes & 0x20) is archive, place
            app = tmp_path / f"app{i}"  # beside the ledger's folder, never above it; one per save
            out = durable_commit(fs, commit(app, Change(str(main), sha256(b"pre\n"), b"post\n")))
            assert isinstance(out, Landed), (place, out)
            assert main.read_bytes() == b"post\n"
            assert os.stat(main).st_file_attributes & 0x20, (place, _kind_of(fs, place))
        finally:
            if place.parent != tmp_path:  # a scratch volume's folder
                shutil.rmtree(place, ignore_errors=True)


def test_V2_F3_a_rollbacks_put_back_keeps_the_files_own_acl(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard (it holds on the base; V2-F3): a save whose second replace fails is rolled back; the first
    file, made private with its own ACL, comes back through the restore's staging name (`create(..., like=...)`)
    with that ACL, not its folder's."""
    from ledger_engine.fs import windows

    led, a = _ledger(tmp_path)
    b = led / "b.bean"
    b.write_bytes(b"b\n")
    user = os.environ["USERNAME"]
    private = ["icacls", str(a), "/inheritance:r", "/grant:r", f"{user}:(F)", "/grant:r", "*S-1-5-32-545:(R)"]
    subprocess.run(private, check=True, capture_output=True)  # noqa: S603 (the system's own tool)
    before = _icacls(a)
    assert not [e for e in before if "(I)" in e], before
    real = windows.WindowsDir.rename

    def rename(self: windows.WindowsDir, src: str, dst: str) -> None:
        if dst == "b.bean":
            raise OSError(errno.EIO, "the second replace fails")
        real(self, src, dst)

    monkeypatch.setattr(windows.WindowsDir, "rename", rename)
    out = _save(fs, tmp_path, Change(str(a), sha256(b"pre\n"), b"A\n"), Change(str(b), sha256(b"b\n"), b"B\n"))
    assert isinstance(out, NotSaved), out
    assert (out.step, out.unchanged, out.open_journal) == (Step.REPLACE, True, False), out
    assert a.read_bytes() == b"pre\n"
    assert _icacls(a) == before, "the put-back took its folder's ACL"


@pytest.mark.parametrize("code", [NOT_SUPPORTED, INVALID_PARAMETER])
def test_Q_B_a_volume_that_keeps_acls_refusing_a_files_acl_is_not_saved_unchanged(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """V2-Q-B (owner, 2026-11-20: "The drive's own flags (Recommended)"): NTFS says it keeps ACLs
    (`FILE_PERSISTENT_ACLS`), so a refused read of the replaced file's DACL is never "this volume keeps none": the
    save is "not saved", every file as it was, its own ACL kept. The base saved it with its folder's ACL."""
    led, main = _ledger(tmp_path)
    user = os.environ["USERNAME"]
    subprocess.run(["icacls", str(main), "/inheritance:r", "/grant:r", f"{user}:(F)"], check=True, capture_output=True)  # noqa: S603, S607
    before = _icacls(main)
    _security_calls(fs, monkeypatch, _refusing("GetSecurityInfo", folder=False, code=code))
    out = _save(fs, tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    _unchanged(out, led, main)
    assert _icacls(main) == before


@pytest.mark.parametrize("code", [NOT_SUPPORTED, INVALID_PARAMETER])
def test_Q_B_a_volume_that_keeps_streams_refusing_a_files_stream_listing_is_not_saved_unchanged(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """V2-Q-B: NTFS says it keeps alternate data streams (`FILE_NAMED_STREAMS`), so a refused listing of the
    replaced file's streams is never "none": "not saved", the file and its stream as they were. The base saved it
    without the stream."""
    led, main = _ledger(tmp_path)
    (led / "main.bean:note").write_bytes(b"a stream\n")
    _security_calls(fs, monkeypatch, _refusing("FileStreamInfo", folder=False, code=code))
    out = _save(fs, tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    _unchanged(out, led, main)
    assert (led / "main.bean:note").read_bytes() == b"a stream\n"


@pytest.mark.parametrize("code", [NOT_SUPPORTED, INVALID_PARAMETER])
def test_Q_B_a_volume_that_keeps_acls_refusing_a_folders_acl_cannot_be_judged_and_refuses_the_save(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """V2-Q-B: the folder rule's ACL check (`handles.acl_denial`) on a volume that keeps ACLs: a refused read of the
    folder's DACL raises (the base answered no entries, so nothing counted as denied), and the save is "not saved"
    before its first effect."""
    led, main = _ledger(tmp_path)
    _security_calls(fs, monkeypatch, _refusing("GetSecurityInfo", folder=True, code=code))
    with fs.open_dir(str(led)) as d, pytest.raises(OSError, match=r"GetSecurityInfo"):
        d.acl()
    out = _save(fs, tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    _unchanged(out, led, main)


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_Q_B_a_volume_is_asked_for_acls_and_streams_only_when_its_flags_say_it_keeps_them(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, volume: str
) -> None:
    """V2-Q-B: on each scratch volume a save lands, and the file's and folder's DACL are asked only where the volume
    sets `FILE_PERSISTENT_ACLS`, the file's streams only where it sets `FILE_NAMED_STREAMS` (FAT32 and exFAT set
    neither: never asked, where the base asked and took the answer's error code as "none")."""
    place = Path(volume) / f"ft-{uuid.uuid4().hex}"
    place.mkdir()
    try:
        main = place / "main.bean"
        main.write_bytes(b"pre\n")
        with fs.open_dir(str(place)) as d:
            from ledger_engine.fs import windows

            assert isinstance(d, windows.WindowsDir)
            flags = d._h.flags()  # pyright: ignore[reportPrivateUsage]
            kind = d.fs_type()
        calls = _security_calls(fs, monkeypatch)
        out = durable_commit(fs, commit(tmp_path / "app", Change(str(main), sha256(b"pre\n"), b"post\n")))
        assert isinstance(out, Landed), out
        assert main.read_bytes() == b"post\n"
        here = [(call, folder) for call, folder, k in calls if k == kind]
        acl_asked = any(call == "GetSecurityInfo" for call, _ in here)
        streams_asked = any(call == "FileStreamInfo" for call, _ in here)
        assert acl_asked is bool(flags & PERSISTENT_ACLS), (kind, hex(flags), here)
        assert streams_asked is bool(flags & NAMED_STREAMS), (kind, hex(flags), here)
    finally:
        shutil.rmtree(place, ignore_errors=True)


def test_Q_B_the_volumes_flags_decide_never_its_name(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2-Q-B: the runner's NTFS with `FILE_PERSISTENT_ACLS` and `FILE_NAMED_STREAMS` taken out of its flags: the
    save lands and asks no DACL and no stream listing (the name `ntfs` decides nothing)."""
    from ledger_engine.fs import windows

    _, main = _ledger(tmp_path)
    assert _kind_of(fs, tmp_path) == "ntfs"
    handle = windows._Handle  # pyright: ignore[reportPrivateUsage]
    real = handle.flags

    def without(self: windows._Handle) -> int:  # pyright: ignore[reportPrivateUsage]
        return real(self) & ~(PERSISTENT_ACLS | NAMED_STREAMS)

    monkeypatch.setattr(handle, "flags", without)
    calls = _security_calls(fs, monkeypatch)
    out = _save(fs, tmp_path, Change(str(main), sha256(b"pre\n"), b"post\n"))
    assert isinstance(out, Landed), out
    assert main.read_bytes() == b"post\n"
    assert calls == [], calls


@pytest.mark.parametrize("where", ["tmp", "volumes"])
def test_V_F6_the_adapters_own_rename_never_leaves_the_name_missing(fs: FileSystem, tmp_path: Path, where: str) -> None:
    """Verifier F6, red team F3: S0126 measured `MoveFileExW`; this measures the adapter's rename by handle
    (`FileRenameInformation` with `ReplaceIfExists`), 300 replaces while another thread asks for the name, on the
    runner's disk and each scratch volume. A missing name is a state a power loss could keep. A rename refused because
    another program held the file at that instant (an antivirus scan on C:, the 2026-11-17 run 12) is counted, not a
    failure: it is the open-file refusal, nothing done."""
    places = [tmp_path] if where == "tmp" else [Path(v) / f"ft-{uuid.uuid4().hex}" for v in _volumes()]
    if not places:
        pytest.skip("no scratch volumes given (FT_TEST_VOLUMES)")
    for place in places:
        place.mkdir(exist_ok=True)
        try:
            target = place / "x.bean"
            target.write_bytes(b"0")
            stop = threading.Event()
            seen = {"looks": 0, "missing": 0}
            look = functools.partial(_watch_name, target, stop, seen)
            watcher = threading.Thread(target=look, daemon=True)
            watcher.start()
            try:
                with fs.open_dir(str(place)) as d:
                    for i in range(300):
                        d.create(".x.bean.ft-s-j.new", str(i).encode(), 0o644)
                        try:
                            d.rename(".x.bean.ft-s-j.new", "x.bean")
                        except PermissionError:  # another program held x.bean that instant (run 12, C:): counted
                            seen["refused"] = seen.get("refused", 0) + 1
                            d.remove(".x.bean.ft-s-j.new")
            finally:
                stop.set()
                watcher.join(DEADLINE)
            # a warning, so the counts show in the CI log's summary for a passing run too (recorded in the journal)
            warnings.warn(f"S0128 rename probe: {place.anchor} {_kind_of(fs, place)}: {seen}", stacklevel=1)
            assert seen["looks"] > 0
            assert seen["missing"] == 0, (str(place), seen)
            assert seen.get("refused", 0) < 300, (str(place), seen)  # the rename works, refused only at instants
        finally:
            if where == "volumes":
                shutil.rmtree(place, ignore_errors=True)


def test_RT_F6_the_engine_lock_file_cannot_be_removed_while_an_engine_holds_it(
    fs: FileSystem, tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """Red team F6: the engine opens its lock file without sharing deletion, so no program (another engine among
    them) removes it while it is held and takes a new lock beside the held one."""
    lock = tmp_path / "locks" / "engine.lock"
    proc = _engine("--engine-lock", str(lock), "--app-folder-id", _app_id(fs, tmp_path))
    procs.append(proc)
    assert _ask(proc, "handshake", 1)["ok"]
    assert _ask(proc, "ping", 2)["ok"]
    with pytest.raises(PermissionError):
        lock.unlink()
    with fs.open_dir(str(tmp_path / "locks")) as d, pytest.raises(OSError, match=r"."):
        d.remove("engine.lock")
    assert lock.is_file()


# -- S0130 (BL-0206): the rename the mirror's pointer swap takes -------------------------------------------------------

GENERIC_READ_ = 0x80000000
SHARE_ALL = 0x7
SHARE_READ_WRITE = 0x3


@contextlib.contextmanager
def held(path: Path, share: int) -> Generator[int]:
    """Another reader holds `path` open with `share` (with deletion: the sidecar's pin; without: Python's `open`)."""
    import _winapi

    h = _winapi.CreateFile(str(path), GENERIC_READ_, share, 0, _winapi.OPEN_EXISTING, 0, 0)
    try:
        yield h
    finally:
        _winapi.CloseHandle(h)


def test_rename_shared_replaces_a_file_a_reader_holds_sharing_deletion_where_the_plain_rename_cannot(
    fs: FileSystem, tmp_path: Path
) -> None:
    """Probed in S0130 (P3, P5) and pinned here on NTFS: while a reader that shares deletion holds `current.txt`, the
    plain rename over it is refused (nothing done) and the rename with POSIX semantics lands; the reader's handle
    still reads the old bytes."""
    import _winapi

    assert _kind_of(fs, tmp_path) == "ntfs"
    (tmp_path / "current.txt").write_bytes(b"old")
    (tmp_path / "a.tmp").write_bytes(b"plain")
    (tmp_path / "b.tmp").write_bytes(b"new")
    with fs.open_dir(str(tmp_path)) as d, held(tmp_path / "current.txt", SHARE_ALL) as h:
        with pytest.raises(OSError, match="rename"):
            d.rename("a.tmp", "current.txt")
        assert (tmp_path / "a.tmp").read_bytes() == b"plain"  # nothing done
        d.rename_shared("b.tmp", "current.txt")
        data, _ = _winapi.ReadFile(h, 16)
        assert data == b"old"
    assert (tmp_path / "current.txt").read_bytes() == b"new"
    assert not (tmp_path / "b.tmp").exists()


def test_rename_shared_is_refused_by_a_reader_that_does_not_share_deletion(fs: FileSystem, tmp_path: Path) -> None:
    (tmp_path / "current.txt").write_bytes(b"old")
    (tmp_path / "b.tmp").write_bytes(b"new")
    with (
        fs.open_dir(str(tmp_path)) as d,
        held(tmp_path / "current.txt", SHARE_READ_WRITE),
        pytest.raises(OSError, match="rename"),
    ):
        d.rename_shared("b.tmp", "current.txt")
    assert (tmp_path / "current.txt").read_bytes() == b"old"
    assert (tmp_path / "b.tmp").read_bytes() == b"new"


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_rename_shared_is_not_offered_off_ntfs_and_does_nothing(fs: FileSystem, volume: str) -> None:
    """FAT32, exFAT and ReFS have no rename with POSIX semantics (probed, S0130's P5): `NotOffered`, decided by the
    volume's flag before any call, nothing done (S0132)."""
    folder = Path(volume) / f"ft-{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        (folder / "current.txt").write_bytes(b"old")
        (folder / "b.tmp").write_bytes(b"new")
        with fs.open_dir(str(folder)) as d, pytest.raises(NotOffered):
            d.rename_shared("b.tmp", "current.txt")
        assert (folder / "current.txt").read_bytes() == b"old"
        assert (folder / "b.tmp").read_bytes() == b"new"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# -- S0132 (BL-0294): where the POSIX rename is offered; clearing the read-only attribute ------------------------------


def _shares() -> list[str]:
    return [v for v in os.environ.get("FT_TEST_SHARE", "").split(";") if v]


def _read_only(path: Path) -> bool:
    return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x1)  # FILE_ATTRIBUTE_READONLY


@pytest.mark.skipif(not _shares(), reason="no network share given (FT_TEST_SHARE)")
@pytest.mark.parametrize("share", _shares() or ["-"])
def test_rename_shared_is_not_offered_on_a_network_share_and_does_nothing(fs: FileSystem, share: str) -> None:
    """ADR-0015 amendment of 2026-11-18 (question 3): a network share is never offered the rename with POSIX
    semantics, whatever its server reports (`remote()` first; Microsoft documents that SMB does not support
    `GetVolumeInformationByHandleW`): `NotOffered`, nothing done, and the plain rename there lands."""
    folder = Path(share) / f"ft-{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        (folder / "current.txt").write_bytes(b"old")
        (folder / "b.tmp").write_bytes(b"new")
        with fs.open_dir(str(folder)) as d:
            with pytest.raises(NotOffered):
                d.rename_shared("b.tmp", "current.txt")
            assert (folder / "current.txt").read_bytes() == b"old"
            assert (folder / "b.tmp").read_bytes() == b"new"
            d.rename("b.tmp", "current.txt")
        assert (folder / "current.txt").read_bytes() == b"new"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.parametrize("answer", ["remote", "no-flag"])
def test_rename_shared_is_decided_by_the_volumes_flag_and_remote_never_by_its_name(
    fs: FileSystem, tmp_path: Path, answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the runner's NTFS (which offers it), a volume that answered remote, or one whose flags lack
    `FILE_SUPPORTS_POSIX_UNLINK_RENAME`, is `NotOffered` before any call, nothing done: the name `ntfs` decides
    nothing (BL-0294)."""
    from ledger_engine.fs import windows

    assert _kind_of(fs, tmp_path) == "ntfs"
    handle = windows._Handle  # pyright: ignore[reportPrivateUsage]
    real = handle.flags

    def remote(self: object) -> bool:
        del self
        return True

    def no_flag(self: windows._Handle) -> int:  # pyright: ignore[reportPrivateUsage]
        return real(self) & ~windows.FILE_SUPPORTS_POSIX_UNLINK_RENAME

    if answer == "remote":
        monkeypatch.setattr(handle, "remote", remote)
    else:
        monkeypatch.setattr(handle, "flags", no_flag)
    (tmp_path / "current.txt").write_bytes(b"old")
    (tmp_path / "b.tmp").write_bytes(b"new")
    with fs.open_dir(str(tmp_path)) as d, pytest.raises(NotOffered):
        d.rename_shared("b.tmp", "current.txt")
    assert (tmp_path / "current.txt").read_bytes() == b"old"
    assert (tmp_path / "b.tmp").read_bytes() == b"new"


@contextlib.contextmanager
def sqlite_reader(path: Path) -> Generator[object]:
    """SQLite's read-only connection on `path` (no delete sharing: S0130's P1)."""
    import sqlite3

    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA schema_version").fetchone()
        yield con
    finally:
        con.close()


def _a_database(path: Path) -> None:
    import sqlite3

    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()


@pytest.mark.parametrize("holder", ["none", "sqlite", "shared", "plain-open"])
def test_clear_readonly_clears_the_attribute_under_every_handle_the_product_holds(
    fs: FileSystem, tmp_path: Path, holder: str
) -> None:
    """BL-0294: the publish clears the read-only attribute on what it removes or replaces; Windows lets it through a
    handle that shares everything while SQLite (no delete sharing), the pin (sharing deletion) or another program's
    plain open holds the file. Only the attribute changes: the bytes stay."""
    f = tmp_path / "gen-1-0000000000000001.db"
    _a_database(f)
    data = f.read_bytes()
    os.chmod(f, 0o444)
    assert _read_only(f)
    with contextlib.ExitStack() as stack:
        if holder == "sqlite":
            stack.enter_context(sqlite_reader(f))
        elif holder == "shared":
            stack.enter_context(held(f, SHARE_ALL))
        elif holder == "plain-open":
            stack.enter_context(open(f, "rb"))
        with fs.open_dir(str(tmp_path)) as d:
            d.clear_readonly(f.name)
            st = d.stat(f.name)
            assert st is not None
            assert not st.locked
        assert not _read_only(f)
    assert f.read_bytes() == data
    with fs.open_dir(str(tmp_path)) as d:
        d.clear_readonly(f.name)  # not read-only: nothing to do
        d.clear_readonly("absent")  # no entry: nothing to do
        d.remove(f.name)
    assert not f.exists()


def test_clear_readonly_follows_no_link(fs: FileSystem, tmp_path: Path) -> None:
    """A junction in the folder: only its own attribute may change, never its target's (the folder is ours, what a
    link leads to is not: ADR-0015 amendment of 2026-11-07)."""
    import _winapi

    target = tmp_path / "target"
    target.mkdir()
    os.chmod(target, 0o444)
    folder = tmp_path / "m"
    folder.mkdir()
    _winapi.CreateJunction(str(target), str(folder / "j"))
    try:
        assert _read_only(target)
        with fs.open_dir(str(folder)) as d:
            d.clear_readonly("j")
        assert _read_only(target)
    finally:
        os.chmod(target, 0o700)  # the read-only attribute cleared


def test_the_ports_remove_and_rename_still_refuse_a_read_only_file(fs: FileSystem, tmp_path: Path) -> None:
    """The save's `locked` outcome relies on this (S0128): only the publish's own `clear_readonly` clears it."""
    f = tmp_path / "main.bean"
    f.write_bytes(b"pre")
    (tmp_path / "new.tmp").write_bytes(b"post")
    os.chmod(f, 0o444)
    try:
        with fs.open_dir(str(tmp_path)) as d:
            with pytest.raises(OSError, match="rename"):
                d.rename("new.tmp", "main.bean")
            with pytest.raises(OSError, match="rename"):
                d.rename_shared("new.tmp", "main.bean")
            with pytest.raises(OSError, match="remove"):
                d.remove("main.bean")
        assert f.read_bytes() == b"pre"
    finally:
        os.chmod(f, 0o644)


@pytest.mark.skipif(not _volumes(), reason="no scratch volumes given (FT_TEST_VOLUMES)")
@pytest.mark.parametrize("volume", _volumes() or ["-"])
def test_clear_readonly_then_the_plain_rename_and_the_removal_land_on_a_scratch_volume(
    fs: FileSystem, volume: str
) -> None:
    """FAT32, exFAT and ReFS: the swap's plain rename over a read-only pointer and the cleanup's removal of a read-only
    file land once the attribute is cleared (refused before)."""
    folder = Path(volume) / f"ft-{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        (folder / "current.txt").write_bytes(b"old")
        (folder / "b.tmp").write_bytes(b"new")
        (folder / "stray").write_bytes(b"s")
        os.chmod(folder / "current.txt", 0o444)
        os.chmod(folder / "stray", 0o444)
        with fs.open_dir(str(folder)) as d:
            with pytest.raises(OSError, match="rename"):
                d.rename("b.tmp", "current.txt")
            d.clear_readonly("current.txt")
            d.rename("b.tmp", "current.txt")
            d.clear_readonly("stray")
            d.remove("stray")
        assert (folder / "current.txt").read_bytes() == b"new"
        assert not (folder / "stray").exists()
    finally:
        for p in folder.iterdir():
            with contextlib.suppress(OSError):
                os.chmod(p, 0o644)
        shutil.rmtree(folder, ignore_errors=True)


# -- S0132 round 1: a linked file is never cleared; the sync's mapping; folders; an entry gone between the opens ------


def test_clear_readonly_never_clears_a_file_with_several_links(fs: FileSystem, tmp_path: Path) -> None:
    """Owner, 2026-11-19: "Never clear a linked file (Recommended)" (verifier V1, red team F1): NTFS keeps the
    attribute on the file, so clearing it through a second name would unprotect the first (a read-only ledger file).
    `EMLINK`, nothing done: the attribute and both names stay."""
    ledger = tmp_path / "main.bean"
    ledger.write_bytes(b"pre\n")
    os.chmod(ledger, 0o444)
    folder = tmp_path / "m"
    folder.mkdir()
    os.link(ledger, folder / "stray.bean")
    try:
        with fs.open_dir(str(folder)) as d:
            with pytest.raises(OSError, match="several links") as e:
                d.clear_readonly("stray.bean")
            assert e.value.errno == errno.EMLINK
        assert _read_only(ledger)
        assert os.stat(ledger).st_nlink == 2
    finally:
        os.chmod(ledger, 0o644)


def test_clear_readonly_clears_a_read_only_folder_which_then_goes(fs: FileSystem, tmp_path: Path) -> None:
    """Red team F3: the folder branch (a read-only folder refuses its removal until cleared; S0132's P7)."""
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o444)
    assert _read_only(ro)
    with fs.open_dir(str(tmp_path)) as d:
        with pytest.raises(OSError, match="rmdir"):
            d.rmdir("ro")
        d.clear_readonly("ro")
        assert not _read_only(ro)
        d.rmdir("ro")
    assert not ro.exists()


def test_clear_readonly_on_an_entry_gone_between_its_two_opens_does_nothing(
    fs: FileSystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red team F4: the entry removed by another program after the attribute read and before the write: nothing
    done, as for no entry."""
    from ledger_engine.fs import windows

    f = tmp_path / "f"
    f.write_bytes(b"x")
    os.chmod(f, 0o444)
    real = windows.WindowsDir._open  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    def racing(self: windows.WindowsDir, name: str, access: int, disposition: int = 1, options: int = 0) -> Any:
        calls.append(access)
        if len(calls) == 2:  # the second open: another program removed it first
            os.chmod(f, 0o644)
            f.unlink()
        return real(self, name, access, disposition, options)

    monkeypatch.setattr(windows.WindowsDir, "_open", racing)
    with fs.open_dir(str(tmp_path)) as d:
        d.clear_readonly("f")
    assert len(calls) == 2
    assert not f.exists()


@pytest.mark.parametrize("remote", [False, True])
def test_a_refused_folder_flush_is_a_plain_os_error_on_every_volume(
    fs: FileSystem, tmp_path: Path, remote: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0134 (owner, 2026-11-19: "Let's remove the best effort sync code, but only if it's dead code and
    unreachable"): `ERROR_INVALID_FUNCTION` from the folder's flush, as the share's server answered (S0132's run 23),
    is a plain `OSError` (`EINVAL`, Windows' own mapping) on a remote volume as on a local one, which every caller
    takes as a failed sync; no caller reaches a share's folder sync any more (the mirror refuses a cache folder
    there)."""
    from ledger_engine.fs import windows

    def is_remote(self: object) -> bool:
        del self
        return remote

    monkeypatch.setattr(windows._Handle, "remote", is_remote)  # pyright: ignore[reportPrivateUsage]
    with fs.open_dir(str(tmp_path)) as d:
        assert isinstance(d, windows.WindowsDir)
        assert d.remote() is remote

        def refused(h: int) -> int:
            del h
            ctypes.set_last_error(1)  # ERROR_INVALID_FUNCTION
            return 0

        monkeypatch.setattr(d.api, "FlushFileBuffers", refused)
        with pytest.raises(OSError, match=r"FlushFileBuffers") as e:
            d.sync()
    assert type(e.value) is OSError
    assert e.value.errno == errno.EINVAL


@pytest.mark.skipif(not _shares(), reason="no network share given (FT_TEST_SHARE)")
def test_remote_is_true_on_every_spelling_of_the_ci_share_and_false_on_local_volumes(
    fs: FileSystem, tmp_path: Path
) -> None:
    """S0134 (BL-0298): `DirHandle.remote()` is `FILE_REMOTE_DEVICE`: True on the CI share by each spelling
    (`FT_TEST_SHARE`: a mapped drive, the UNC path, the UNC path by the machine's name), False on the runner's NTFS
    and the scratch volumes (`FT_TEST_VOLUMES`)."""
    shares = _shares()
    assert len(shares) == 3, shares  # the CI share step fails the job when it cannot make them (S0132 round 1, V5)
    for share in shares:
        with fs.open_dir(share) as d:
            assert d.remote() is True, share
    for local in [str(tmp_path), *[v for v in os.environ.get("FT_TEST_VOLUMES", "").split(";") if v]]:
        with fs.open_dir(local) as d:
            assert d.remote() is False, local
