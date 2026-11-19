"""A crash, an I/O error or a power loss at every step of a publish job (`ledger_engine.mirror.publish`;
`design/specs/mirror/publish.md` §9; S0087, BL-0113), over the in-memory file system with its durable layer
(`tests/fakes/memfs.py`) and the fake store (`tests/mirror/fake_store.py`). Written from the spec's lines:

- §9 "No step leaves a reader a partial, incomplete or torn generation: `current.db` names a file only after its data
  is durable (step 5 before step 8), and the old link stays on a whole file until the new one replaces it";
- §4 step 6 (availability): after a power loss `current.db` names an existing file with no journal beside it;
- §9 "The next job": whatever a cut job left, the next one removes it and publishes; the folder then holds only what
  §6 keeps.

The oracle reads the folder as a reader does (§7): `current.db` absent, or a link to exactly a generation name whose
file the store accepts as whole and complete (no journal beside it). Every sweep runs in both forms (S0130, BL-0206):
the link (macOS, Linux) and Windows' pointer file `current.txt`, whose whole content is a generation name, over the
fake's Windows semantics (no link made; a file a reader holds cannot be removed or renamed). The sweep runs the job once
to count its effects, then once per effect with the fault there, and checks the state left and every state a power loss
may leave from it.
"""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator

import pytest

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, digest
from ledger_engine.mirror import publish as pb
from tests.fakes.memfs import Crash, MemDir, MemFS
from tests.mirror.fake_store import FakeStore
from tests.mirror.publish_support import BUILT_AT, ENGINE_BUILD, KEY, TODAY

pytestmark = [pytest.mark.inv("INV-05")]

CACHE = "/cache"
MIRROR = f"{CACHE}/workspaces/{KEY}/mirror"
NOW = 10**18
FP = Fingerprint("/L/main.bean", (("/L/main.bean", Content("0" * 64)),), (), (), ())


@pytest.fixture(params=[False, True], ids=["link", "pointer"])
def windows(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


def world(windows: bool = False) -> MemFS:
    fs = MemFS(windows=windows)
    fs.outside_mkdirs(CACHE)
    return fs


def pointer(fs: MemFS) -> str:
    """The name that says which generation is current: the link on POSIX, the pointer file on Windows."""
    return "current.txt" if fs.windows else "current.db"


def job(fs: MemFS, recheck: pb.Recheck = "same") -> pb.Published | pb.NotPublished:
    store = FakeStore()
    with pb.open_folder(fs, CACHE, KEY) as d:
        cleaned = pb.clean(d, store, NOW)
        return pb.publish(
            d,
            cleaned,
            store,
            ([], [], {}),
            FP,
            floor=0,
            today=TODAY,
            built_at=BUILT_AT,
            engine_build=ENGINE_BUILD,
            recheck=lambda: recheck,
            by="rebuild",
        )


def seen_by_a_reader(fs: MemFS, *, folder_at_current: bool = False) -> str | None:
    """What a reader reads (§7): None when there is no published mirror; else the generation's name. A `current.db`
    that leads to anything but a whole, complete generation fails the test (with `folder_at_current`, a folder left
    there by someone else is no published mirror: readers answer `not_published`)."""
    try:
        d = fs.open_dir(MIRROR)
    except OSError:
        return None
    with d:
        st = d.stat(pointer(fs))
        if st is None or (folder_at_current and st.kind is Kind.DIR):
            return None
        if fs.windows:
            assert st.kind is Kind.FILE, f"current.txt is a {st.kind}"
            data = d.read(pointer(fs))
            assert data is not None
            target = data.decode("ascii")  # nothing but a generation name: no newline, no padding
        else:
            target = d.readlink(pb.CURRENT)
        assert target is not None, "current.db is not a link"
        assert pb.gen_of(target) is not None, f"current.db names {target!r}"
        facts = FakeStore().meta(d, target)
        assert facts is not None, f"current.db leads to {target}, which is not a whole mirror"
        assert facts.build_complete
        assert facts.ledger_version == f"{pb.gen_of(target).number}/{digest(FP)}"  # type: ignore[union-attr]
        return target


def states(fs: MemFS) -> Iterator[MemFS]:
    yield fs
    yield from fs.power_loss_states()


def assert_clean_after_the_next_job(fs: MemFS) -> None:
    out = job(fs)
    assert isinstance(out, pb.Published), out
    with fs.open_dir(MIRROR) as d:
        left = set(d.names())
        if fs.windows:
            assert d.read(pointer(fs)) == out.gen.name.encode("ascii")
        else:
            assert d.readlink(pb.CURRENT) == out.gen.name
    gens = {n for n in left if pb.gen_of(n) is not None}
    assert left == {pointer(fs), *gens}, left  # no journal, no temporary pointer, nothing else
    assert len(gens) <= 4
    assert all(pb.gen_of(n).number <= out.gen.number for n in gens)  # type: ignore[union-attr]
    for s in states(fs):
        seen_by_a_reader(s)


def effects_of(prepare: int, *, with_junk: bool = False, windows: bool = False) -> int:
    fs, _ = prepared(prepare, with_junk=with_junk, windows=windows)
    n = len(fs.effects)
    assert isinstance(job(fs), pb.Published)
    return len(fs.effects) - n


def junk(fs: MemFS) -> None:
    """What the rules do not name, left by someone in the folder: a folder holding a file and a link to a folder
    outside, a link at a generation's name, a stray `current.db-wal`."""
    fs.outside_mkdirs("/outside")
    fs.outside_write("/outside/keep", b"the user's")
    fs.outside_mkdirs(f"{MIRROR}/f/g")
    fs.outside_write(f"{MIRROR}/f/a", b"a")
    fs.outside_symlink(f"{MIRROR}/f/g/lnk", "/outside")
    fs.outside_symlink(f"{MIRROR}/gen-1-{'f' * 16}.db", "/outside/keep")
    fs.outside_write(f"{MIRROR}/current.db-wal", b"w")
    if fs.windows:  # a `current.db` someone left is an unknown entry there (the pointer is `current.txt`)
        fs.outside_write(f"{MIRROR}/current.db", b"not ours to read")
        fs.outside_write(f"{MIRROR}/current.txt.{'a' * 16}.tmp", f"gen-1-{'f' * 16}.db".encode())


def prepared(prepare: int, *, with_junk: bool, windows: bool = False) -> tuple[MemFS, list[str]]:
    fs = world(windows)
    published: list[str] = []
    for _ in range(prepare):
        out = job(fs)
        assert isinstance(out, pb.Published)
        published.append(out.gen.name)
    if with_junk:
        junk(fs)
    return fs, published


@pytest.mark.parametrize(
    ("prepare", "with_junk"),
    [(0, False), (1, False), (2, False), (3, False), (1, True)],
    ids=["first", "second", "third", "fourth-removes-the-oldest", "second-with-junk"],
)
def test_a_death_at_every_effect_never_leaves_a_reader_a_partial_mirror(
    prepare: int, with_junk: bool, windows: bool
) -> None:
    total = effects_of(prepare, with_junk=with_junk, windows=windows)
    assert total > 10
    for i in range(total):
        fs, published = prepared(prepare, with_junk=with_junk, windows=windows)
        fs.inject(len(fs.effects) + i, crash=True)
        with contextlib.suppress(Crash):
            job(fs)
        for s in states(fs):
            seen = seen_by_a_reader(s)
            if s is fs and published and i < total:
                # a death leaves the folder as it is: a reader sees the old generation or, after the rename, the new
                assert seen is not None
            assert_clean_after_the_next_job(s)
            if with_junk:
                assert s.file("/outside/keep") == b"the user's"  # never followed, never removed
                assert s.names_in("/outside") == ["keep"]


@pytest.mark.parametrize(
    ("prepare", "with_junk"),
    [(0, False), (1, False), (2, False), (3, False), (1, True)],
    ids=["first", "second", "third", "fourth-removes-the-oldest", "second-with-junk"],
)
def test_an_io_error_at_every_effect_never_leaves_a_reader_a_partial_mirror(
    prepare: int, with_junk: bool, windows: bool
) -> None:
    total = effects_of(prepare, with_junk=with_junk, windows=windows)
    for i in range(total):
        fs, _ = prepared(prepare, with_junk=with_junk, windows=windows)
        before = seen_by_a_reader(fs)
        fs.inject(len(fs.effects) + i, crash=False, err=errno.EIO)
        try:
            out = job(fs)
        except OSError:
            out = None
        after = seen_by_a_reader(fs)
        if before is not None:
            assert after is not None  # a failed job never takes the published mirror away
        if out is None or isinstance(out, pb.NotPublished):
            assert after == before
        for s in states(fs):
            seen_by_a_reader(s)
        assert_clean_after_the_next_job(fs)
        if with_junk:
            assert fs.names_in("/outside") == ["keep"]


def test_a_refused_publish_leaves_the_current_generation_and_its_file_for_the_next_cleanup(windows: bool) -> None:
    fs = world(windows)
    first = job(fs)
    assert isinstance(first, pb.Published)
    whys: tuple[pb.Recheck, ...] = ("moved", "paused")
    for why in whys:
        assert job(fs, why) == pb.NotPublished(why)
        assert seen_by_a_reader(fs) == first.gen.name
    assert_clean_after_the_next_job(fs)


def test_a_power_loss_after_a_publish_lands_on_the_new_or_the_previous_generation(windows: bool) -> None:
    fs = world(windows)
    first = job(fs)
    second = job(fs)
    assert isinstance(first, pb.Published)
    assert isinstance(second, pb.Published)
    seen = {seen_by_a_reader(s) for s in fs.power_loss_states()}
    assert seen == {first.gen.name, second.gen.name}  # the rename may be lost; the previous one is whole and kept


# -- round 1 (S0087): the review's cases over the fake ---------------------------------------------------------------


def test_another_volume_inside_the_folder_is_never_entered_or_removed_in_any_crash(windows: bool) -> None:
    """Red team F1, on the fake: a volume mounted at `mirror/mnt` keeps its file through a death at every effect of
    the next job and every power loss, and the job still publishes."""
    base = world(windows)
    assert isinstance(job(base), pb.Published)
    total = None
    for i in range(64):
        fs = world(windows)
        assert isinstance(job(fs), pb.Published)
        fs.outside_mkdirs(f"{MIRROR}/mnt/sub")
        fs.outside_write(f"{MIRROR}/mnt/sub/vol.txt", b"another volume's")
        fs.outside_mount(f"{MIRROR}/mnt", 2)
        start = len(fs.effects)
        fs.inject(start + i, crash=True)
        try:
            out = job(fs)
        except Crash:
            out = None
        if out is not None:
            total = i
            assert isinstance(out, pb.Published)
        for s in states(fs):
            assert s.file(f"{MIRROR}/mnt/sub/vol.txt") == b"another volume's"
            seen_by_a_reader(s)
        if out is not None:
            break
    assert total is not None


def test_a_mirror_folder_on_another_volume_is_refused(windows: bool) -> None:
    fs = world(windows)
    fs.outside_mkdirs(f"{MIRROR}/sub")
    fs.outside_write(f"{MIRROR}/sub/vol.txt", b"another volume's")
    fs.outside_mount(MIRROR, 2)
    with pytest.raises(OSError, match="another volume"):
        job(fs)
    assert fs.file(f"{MIRROR}/sub/vol.txt") == b"another volume's"


def test_a_journal_the_cleanup_cannot_remove_still_raises_the_next_number(windows: bool) -> None:
    """Verifier V5 (`top_ignores_journals`): §3 "every `N` in a generation name or a journal name in the folder after
    the cleanup"."""
    fs = world(windows)
    assert isinstance(job(fs), pb.Published)
    stuck = f"{MIRROR}/gen-9-{'e' * 16}.db-journal"
    fs.outside_write(stuck, b"x")
    # its removal fails: it stays (Windows: a reader holds it; the read-only attribute no longer keeps a file there,
    # S0132, BL-0294)
    fs.outside_hold(stuck) if fs.windows else fs.outside_lock(stuck)
    out = job(fs)
    assert isinstance(out, pb.Published)
    assert out.gen.number == 10


def test_a_folder_at_current_db_that_cannot_be_removed_is_moved_aside_in_every_crash(windows: bool) -> None:
    """Red team F6, on the fake: a locked file inside keeps the folder; the swap moves it aside."""
    for i in range(64):
        fs = world(windows)
        fs.outside_mkdirs(f"{MIRROR}/{pointer(fs)}")
        fs.outside_write(f"{MIRROR}/{pointer(fs)}/x", b"x")
        fs.outside_lock(f"{MIRROR}/{pointer(fs)}/x")
        fs.inject(len(fs.effects) + i, crash=True)
        try:
            out = job(fs)
        except Crash:
            out = None
        for s in states(fs):
            seen_by_a_reader(s, folder_at_current=True)
        if out is not None:
            assert isinstance(out, pb.Published)
            assert seen_by_a_reader(fs) == out.gen.name
            break
    else:
        pytest.fail("the job never finished")


# -- S0130 (BL-0206): Windows' pointer file and the retried removal --------------------------------------------------


def published_names(fs: MemFS) -> set[str]:
    return {n for n in fs.names_in(MIRROR) if pb.gen_of(n) is not None}


def test_a_generation_a_reader_holds_survives_the_cleanup_and_goes_at_the_next_after_it_closes() -> None:
    """ADR-0015 amendment of 2026-10-25: a removal that fails because a file is open is expected and retried at the
    next collection, never reported as an error. A reader holds the first generation while four more are published
    (it would go at the third: only the current, the previous and 2 young ones stay); each cleanup counts it failed
    and the jobs publish; once released, the next cleanup removes it."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_hold(f"{MIRROR}/{first.gen.name}")
    for _ in range(4):
        assert isinstance(job(fs), pb.Published)
    assert first.gen.name in published_names(fs)
    with fs.open_dir(MIRROR) as d:
        cleaned = pb.clean(d, FakeStore(), NOW)
    assert cleaned.failed == 1
    fs.outside_release(f"{MIRROR}/{first.gen.name}")
    assert isinstance(job(fs), pb.Published)
    assert first.gen.name not in published_names(fs)
    assert len(published_names(fs)) <= 4


def test_a_replaced_generation_a_reader_holds_still_gets_its_replacement_time() -> None:
    """§4 step 8: the replaced generation's modification time is set at its replacement, while a reader holds it
    (Windows lets a handle that shares everything set it; S0130's P1)."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    path = f"{MIRROR}/{first.gen.name}"
    fs.outside_hold(path)
    ino = fs.resolve(path).identity
    assert ino is not None
    before = fs.mtimes[ino[1]]
    assert isinstance(job(fs), pb.Published)
    assert fs.mtimes[ino[1]] > before


NAME_SLOT = b"gen-1-" + b"0" * 16 + b".db"


@pytest.mark.parametrize(
    "content",
    [b"", NAME_SLOT + b"\n", b" " + NAME_SLOT, NAME_SLOT + b"\0", b"gen-1.db", b"x" * 4096, b"\xff\xfe"],
    ids=["empty", "newline", "space", "nul", "old-name", "long", "not-ascii"],
)
def test_a_pointer_that_is_not_exactly_a_generation_name_is_no_current_generation(content: bytes) -> None:
    """§6 "Kept" on Windows: `current.txt` names the current generation only when its whole content is exactly a
    generation name of a regular file of the folder; anything else is none: the cleanup keeps no generation by it, a
    reader sees no published mirror, and the next job publishes over it."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_write(f"{MIRROR}/current.txt", content.replace(NAME_SLOT, first.gen.name.encode("ascii")))
    with fs.open_dir(MIRROR) as d:
        assert pb.current(d) is None
    assert pb.read_published(fs, CACHE, KEY, FakeStore()) is None
    assert_clean_after_the_next_job(fs)


def test_a_pointer_naming_a_generation_that_is_gone_or_not_a_file_is_no_current_generation() -> None:
    fs = world(windows=True)
    assert isinstance(job(fs), pb.Published)
    gone = f"gen-7-{'c' * 16}.db"
    fs.outside_write(f"{MIRROR}/current.txt", gone.encode("ascii"))
    with fs.open_dir(MIRROR) as d:
        assert pb.current(d) is None
    fs.outside_mkdirs(f"{MIRROR}/{gone}")
    with fs.open_dir(MIRROR) as d:
        assert pb.current(d) is None
    assert_clean_after_the_next_job(fs)


def test_a_link_at_the_pointer_is_no_current_generation_and_is_removed_as_a_link() -> None:
    """A link (a junction on Windows) at `current.txt` is never followed or read through."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_mkdirs("/outside")
    fs.outside_write("/outside/p", first.gen.name.encode("ascii"))
    fs.outside_remove(f"{MIRROR}/current.txt")
    fs.outside_symlink(f"{MIRROR}/current.txt", "/outside/p")
    with fs.open_dir(MIRROR) as d:
        assert pb.current(d) is None
    assert_clean_after_the_next_job(fs)
    assert fs.file("/outside/p") == first.gen.name.encode("ascii")


def test_no_link_is_made_on_windows() -> None:
    """The pointer form follows the volume's answer (`caps().links`), never a refused link: no effect of a Windows
    job is a link."""
    fs = world(windows=True)
    assert isinstance(job(fs), pb.Published)
    assert isinstance(job(fs), pb.Published)
    assert not [e for e in fs.effects if e.startswith("symlink")], fs.effects


def test_the_pointer_is_written_whole_and_synced_before_its_rename() -> None:
    """§4 step 8 on Windows: the temporary pointer is created with its data synced (the port's `create`: entry,
    write, data sync) before the one rename over `current.txt`. The sweeps' oracle checks every state; this pins the
    order."""
    fs = world(windows=True)
    assert isinstance(job(fs), pb.Published)
    start = len(fs.effects)
    assert isinstance(job(fs), pb.Published)
    tail = [e for e in fs.effects[start:] if "current.txt" in e]
    kinds = [e.partition(" ")[0] for e in tail]
    # S0132 (BL-0294): the read-only attribute of `current.txt` cleared just before the rename over it (no name moves)
    assert kinds[-5:] == ["create", "write", "fsync", "clear_readonly", "move"], tail


def test_a_pointer_renamed_before_its_data_is_synced_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweeps are not vacuous for the pointer's sync: a temporary pointer whose bytes are written after its
    synced empty file (not durable at the rename) leaves, after a power loss, a `current.txt` that names no
    generation (empty or torn), which the oracle refuses."""
    create = MemDir.create

    def unsynced(self: MemDir, name: str, data: bytes, mode: int, like: str | None = None) -> tuple[int, int]:
        if not name.startswith("current.txt"):
            return create(self, name, data, mode, like)
        out = create(self, name, b"", mode, like)
        self.write_in(name, data)
        return out

    monkeypatch.setattr(MemDir, "create", unsynced)
    with pytest.raises(AssertionError, match=r"current\.txt|names"):
        test_a_power_loss_after_a_publish_lands_on_the_new_or_the_previous_generation(windows=True)


def without_posix_rename(fs: MemFS, how: str) -> None:
    """The cache's drive lacks the rename with POSIX semantics: its volume flag clear (ReFS, FAT32, exFAT, an older
    Windows), or a network share, which may report NTFS and the flag (S0132, BL-0294)."""
    with fs.open_dir(CACHE) as d:
        if how == "no-flag":
            fs.no_posix_rename.add(d.device())
        elif how == "remote":
            fs.fs_types[d.device()] = "ntfs"  # what a share's server may report: never asked (BL-0294)
            fs.remote.add(d.device())


@pytest.mark.parametrize("how", ["no-flag", "remote"])
def test_without_the_posix_rename_the_pointer_swap_takes_the_plain_rename(how: str) -> None:
    """§4 step 8, §11 "Limit (Windows without the POSIX rename)": on a Windows volume that does not offer the rename
    with POSIX semantics (its flag clear, or a network share), the swap takes the plain rename, stated, and
    publishes."""
    fs = world(windows=True)
    without_posix_rename(fs, how)
    first = job(fs)
    second = job(fs)
    assert isinstance(first, pb.Published)
    assert isinstance(second, pb.Published)
    assert seen_by_a_reader(fs) == second.gen.name


@pytest.mark.parametrize("how", ["offered", "no-flag", "remote"])
def test_a_reader_holding_the_pointer_without_sharing_deletion_refuses_the_swap_and_changes_nothing(how: str) -> None:
    """§11 "Limit (a program holding the pointer)": a program that holds `current.txt` open without sharing deletion
    refuses the swap's rename on every volume: the job raises, `current.txt` still names the generation it named,
    and once the program closes it the next job publishes."""
    fs = world(windows=True)
    without_posix_rename(fs, how)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_hold(f"{MIRROR}/current.txt")
    with pytest.raises(PermissionError, match="in use"):
        job(fs)
    assert seen_by_a_reader(fs) == first.gen.name
    fs.outside_release(f"{MIRROR}/current.txt")
    assert_clean_after_the_next_job(fs)


def test_on_ntfs_a_refused_posix_rename_is_never_retried_as_the_plain_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    """The brief: the form is never guessed from a failure. Only `NotOffered` (the volume lacks the rename) takes the
    plain rename; any other refusal of `rename_shared` ends the job, even where the plain rename would land (S0130
    round 1, verifier V5: a catch-all fallback survived every other test)."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)

    def refused(self: MemDir, src: str, dst: str) -> None:
        del self, src, dst
        raise PermissionError(errno.EACCES, "refused")

    monkeypatch.setattr(MemDir, "rename_shared", refused)
    with pytest.raises(PermissionError, match="refused"):
        job(fs)
    assert seen_by_a_reader(fs) == first.gen.name


# -- S0132 (BL-0294): read-only entries in the mirror folder on Windows ------------------------------------------------


def read_only_junk(fs: MemFS) -> None:
    """Windows: what has the read-only attribute in the folder (set in Properties, or by a copy or restore tool): the
    pointer, every generation, a stale temporary pointer, an unknown file, and a folder holding a read-only file. The
    fake's `outside_lock` is the attribute in Windows mode."""
    for n in fs.names_in(MIRROR):
        fs.outside_lock(f"{MIRROR}/{n}")
    fs.outside_write(f"{MIRROR}/current.txt.{'b' * 16}.tmp", f"gen-1-{'f' * 16}.db".encode())
    fs.outside_lock(f"{MIRROR}/current.txt.{'b' * 16}.tmp")
    fs.outside_write(f"{MIRROR}/stray.bin", b"s")
    fs.outside_lock(f"{MIRROR}/stray.bin")
    fs.outside_mkdirs(f"{MIRROR}/ro")
    fs.outside_write(f"{MIRROR}/ro/inside", b"i")
    fs.outside_lock(f"{MIRROR}/ro/inside")
    fs.outside_lock(f"{MIRROR}/ro")  # Windows refuses to remove a read-only folder (S0132's P7)


def read_only_prepared(prepare: int) -> MemFS:
    fs, _ = prepared(prepare, with_junk=False, windows=True)
    read_only_junk(fs)
    return fs


def test_a_read_only_pointer_neither_refuses_the_swap_nor_stays() -> None:
    """ADR-0015 amendment of 2026-11-18 (question 4): the swap clears the read-only attribute on `current.txt` before
    the rename over it; on the base every swap was refused until the user cleared it."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_lock(f"{MIRROR}/current.txt")
    second = job(fs)
    assert isinstance(second, pb.Published), second
    assert seen_by_a_reader(fs) == second.gen.name


def test_read_only_generations_go_in_their_turn_and_the_folder_stays_bounded() -> None:
    """§6 on Windows: a read-only generation is removed when the keep set drops it (the disk stays bounded), and an
    unknown read-only entry, a read-only temporary pointer and a folder holding a read-only file are removed at the
    first cleanup."""
    fs = read_only_prepared(3)
    for _ in range(4):
        assert isinstance(job(fs), pb.Published)
    assert_clean_after_the_next_job(fs)


@pytest.mark.parametrize("prepare", [1, 3])
def test_a_death_at_every_effect_among_read_only_entries_never_leaves_a_reader_a_partial_mirror(prepare: int) -> None:
    """§9 with read-only entries (Windows): a crash at every effect, the clears included, and every state a power loss
    leaves from it: a reader sees the old or the new generation, and the next job publishes and cleans."""
    total = len(_effects_read_only(prepare))
    assert any(e.startswith("clear_readonly") for e in _effects_read_only(prepare))
    for i in range(total):
        fs = read_only_prepared(prepare)
        fs.inject(len(fs.effects) + i, crash=True)
        with contextlib.suppress(Crash):
            job(fs)
        for s in states(fs):
            assert seen_by_a_reader(s) is not None
            assert_clean_after_the_next_job(s)


@pytest.mark.parametrize("prepare", [1, 3])
def test_an_io_error_at_every_effect_among_read_only_entries_changes_nothing_a_reader_sees_unless_published(
    prepare: int,
) -> None:
    total = len(_effects_read_only(prepare))
    for i in range(total):
        fs = read_only_prepared(prepare)
        before = seen_by_a_reader(fs)
        fs.inject(len(fs.effects) + i, crash=False, err=errno.EIO)
        try:
            out = job(fs)
        except OSError:
            out = None
        after = seen_by_a_reader(fs)
        assert after is not None
        if out is None or isinstance(out, pb.NotPublished):
            assert after == before
        assert_clean_after_the_next_job(fs)


def _effects_read_only(prepare: int) -> list[str]:
    fs = read_only_prepared(prepare)
    n = len(fs.effects)
    assert isinstance(job(fs), pb.Published)
    return fs.effects[n:]


def test_a_read_only_file_at_the_mirror_folders_name_is_replaced_by_the_folder() -> None:
    """§2 "The folder itself" on Windows: a file at `mirror/` is removed, even with the read-only attribute."""
    fs = world(windows=True)
    fs.outside_mkdirs(f"{CACHE}/workspaces/{KEY}")
    fs.outside_write(MIRROR, b"not a folder")
    fs.outside_lock(MIRROR)
    out = job(fs)
    assert isinstance(out, pb.Published), out
    assert seen_by_a_reader(fs) == out.gen.name


def test_the_link_form_makes_no_clear_and_a_locked_entry_still_stays() -> None:
    """macOS and Linux: `clear_readonly` makes no call (a mode never stops a removal there); `uchg` (`outside_lock`)
    is not the read-only attribute and is never cleared, so such an entry stays (publish.md §11)."""
    fs = world(windows=False)
    assert isinstance(job(fs), pb.Published)
    stuck = f"{MIRROR}/stray.bin"
    fs.outside_write(stuck, b"s")
    fs.outside_lock(stuck)
    n = len(fs.effects)
    assert isinstance(job(fs), pb.Published)
    assert not any(e.startswith("clear_readonly") for e in fs.effects[n:])
    assert "stray.bin" in fs.names_in(MIRROR)


def test_a_ledger_files_read_only_refusal_is_unchanged_on_windows() -> None:
    """The save's `locked` outcome relies on the port's `remove` and `rename` refusing a read-only file: only the
    publish's own `clear_readonly` clears it, never those (S0132's design note)."""
    fs = MemFS(windows=True)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/main.bean", b"pre")
    fs.outside_write("/L/new.tmp", b"post")
    fs.outside_lock("/L/main.bean")
    with fs.open_dir("/L") as d:
        st = d.stat("main.bean")
        assert st is not None
        assert st.locked
        with pytest.raises(PermissionError):
            d.rename("new.tmp", "main.bean")
        with pytest.raises(PermissionError):
            d.rename_shared("new.tmp", "main.bean")
        with pytest.raises(PermissionError):
            d.remove("main.bean")
    assert fs.file("/L/main.bean") == b"pre"


def test_a_read_only_temporary_pointer_at_the_swaps_own_name_is_cleared_and_replaced() -> None:
    """§4 step 8: an entry already at the temporary pointer's name is ours and removed first, even read-only. The
    cleanup removes stale ones before the build, so only a program writing between the cleanup and the swap leaves one
    there (self-mutation: dropping this clear survived every sweep)."""
    fs = world(windows=True)
    assert isinstance(job(fs), pb.Published)
    tag = "c" * 16
    store = FakeStore()
    with pb.open_folder(fs, CACHE, KEY) as d:
        cleaned = pb.clean(d, store, NOW)
        fs.outside_write(f"{MIRROR}/current.txt.{tag}.tmp", b"stale")
        fs.outside_lock(f"{MIRROR}/current.txt.{tag}.tmp")
        out = pb.publish(
            d,
            cleaned,
            store,
            ([], [], {}),
            FP,
            floor=0,
            today=TODAY,
            built_at=BUILT_AT,
            engine_build=ENGINE_BUILD,
            recheck=lambda: "same",
            by="rebuild",
            tag=tag,
        )
    assert isinstance(out, pb.Published), out
    assert seen_by_a_reader(fs) == out.gen.name


# -- S0132 (the owner, 2026-11-19: "Best effort on a share (Recommended)"): a share that refuses folder syncs ---------


def seen_on_a_share(fs: MemFS) -> str | None:
    """What a reader reads where no folder sync makes anything durable (publish.md §11 "Mode (a cache folder on a
    network share)"): after a power loss the pointer may name a generation whose entry was lost, or one with its
    journal beside it; the reader then finds no published mirror (`current` None, or the store refuses it), which
    costs a rebuild. What it does read is always a whole, complete generation of this stamp."""
    try:
        d = fs.open_dir(MIRROR)
    except OSError:
        return None
    with d:
        gen = pb.current(d)
        facts = None if gen is None else FakeStore().meta(d, gen.name)
        if gen is None or facts is None:
            return None
        assert facts.build_complete
        assert facts.ledger_version == f"{gen.number}/{digest(FP)}"
        return gen.name


def assert_publishes_again_on_a_share(fs: MemFS, *, power_loss: bool = True) -> None:
    """The next job publishes and leaves only what §6 keeps; with `power_loss`, every state a power loss may then
    leave is read (one level: nothing on such a share is ever durable, so the states multiply)."""
    out = job(fs)
    assert isinstance(out, pb.Published), out
    assert seen_on_a_share(fs) == out.gen.name
    left = set(fs.names_in(MIRROR))
    gens = {n for n in left if pb.gen_of(n) is not None}
    # a journal a power loss brought back beside a kept generation stays (§6: such a file is not read, not removed)
    journals = {f"{g}{x}" for g in gens for x in pb.JOURNALS} & left
    assert left == {"current.txt", *gens, *journals}, left
    if power_loss:
        for s in fs.power_loss_states():
            seen_on_a_share(s)


def share_world() -> MemFS:
    """The cache folder on a network share whose server refuses a folder's sync (the 2026-11-18 run 23)."""
    fs = world(windows=True)
    with fs.open_dir(CACHE) as d:
        fs.remote.add(d.device())
        fs.sync_refused.add(d.device())
    return fs


def test_on_a_share_that_refuses_folder_syncs_the_publish_is_best_effort_and_noted_once() -> None:
    """publish.md §11 "Mode (a cache folder on a network share)": every folder sync of the publish is refused there;
    each counts as done for the mirror, the job publishes (the plain rename), and the note says so for the log."""
    fs = share_world()
    note = pb.SyncNote()
    store = FakeStore()
    with pb.open_folder(fs, CACHE, KEY, note) as d:
        cleaned = pb.clean(d, store, NOW)
        out = pb.publish(
            d,
            cleaned,
            store,
            ([], [], {}),
            FP,
            floor=0,
            today=TODAY,
            built_at=BUILT_AT,
            engine_build=ENGINE_BUILD,
            recheck=lambda: "same",
            by="rebuild",
            note=note,
        )
    assert isinstance(out, pb.Published), out
    assert note.refused
    assert seen_on_a_share(fs) == out.gen.name
    assert_publishes_again_on_a_share(fs)


def test_a_local_volumes_failed_folder_sync_still_fails_the_publish() -> None:
    """The must-not-pass of the mode: a folder sync refused with any other error (here `EIO` at step 6, on a local
    volume) ends the job unpublished, as before; only `SyncRefused` is best effort."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    start = len(fs.effects)
    assert isinstance(job(fs), pb.Published)
    labels = fs.effects[start:]
    step6 = max(i for i, e in enumerate(labels) if e.startswith("sync "))  # the last folder sync: step 6
    fs2 = world(windows=True)
    assert isinstance(job(fs2), pb.Published)
    fs2.inject(len(fs2.effects) + step6, crash=False, err=errno.EIO)
    with pytest.raises(OSError, match="injected"):
        job(fs2)
    assert seen_by_a_reader(fs2) is not None  # the job ended before its swap: the earlier generation stays current


@pytest.mark.parametrize("prepare", [0, 1])
def test_a_death_at_every_effect_on_a_share_that_refuses_syncs_never_leaves_a_reader_a_partial_mirror(
    prepare: int,
) -> None:
    """§9 on such a share: a crash at every effect, and every state a power loss leaves (nothing a refused sync
    would have made durable is): a reader sees a whole generation or none, and the next job publishes and cleans."""
    base = share_world()
    for _ in range(prepare):
        assert isinstance(job(base), pb.Published)
    n = len(base.effects)
    assert isinstance(job(base), pb.Published)
    total = len(base.effects) - n
    for i in range(total):
        fs = share_world()
        for _ in range(prepare):
            assert isinstance(job(fs), pb.Published)
        fs.inject(len(fs.effects) + i, crash=True)
        with contextlib.suppress(Crash):
            job(fs)
        # nothing on such a share is ever durable, so a power loss's states multiply with each job: every one after
        # the first job's crash, the crashed state alone after later jobs
        for s in states(fs) if prepare == 0 else [fs]:
            seen_on_a_share(s)
            assert_publishes_again_on_a_share(s, power_loss=False)  # the crashed state's power losses: above


# -- S0132 round 1 (verifier V1, red team F1; owner, 2026-11-19: "Never clear a linked file (Recommended)") ----------


@pytest.mark.parametrize("name", ["stray.bean", "current.txt"])
def test_a_hard_link_in_the_mirror_folder_never_clears_a_ledger_files_read_only_attribute(name: str) -> None:
    """The reviewers' repros: a read-only ledger file with a second name in the mirror folder (left there, or at the
    pointer's name) keeps its attribute and both its names through the next job, so the save still answers `locked`
    and `hard_linked`. The cleanup counts the link as a removal that failed; at the pointer's name the swap fails
    truthfully (nothing published by it)."""
    fs = world(windows=True)
    first = job(fs)
    assert isinstance(first, pb.Published)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/main.bean", b"2026-01-01 open Assets:Cash\n")
    fs.outside_lock("/L/main.bean")  # the user marked the ledger file read-only: the save answers `locked`
    if name == "current.txt":
        fs.outside_remove(f"{MIRROR}/current.txt")
    fs.outside_link("/L/main.bean", f"{MIRROR}/{name}")
    if name == "current.txt":
        with pytest.raises(OSError, match="several links"):  # the swap fails truthfully: nothing published by it
            job(fs)
    else:
        assert isinstance(job(fs), pb.Published)
    with fs.open_dir("/L") as d:
        st = d.stat("main.bean")
    assert st is not None
    assert st.locked, "the ledger file's read-only attribute was cleared through a hard link in mirror/"
    assert st.nlink == 2
    assert fs.file("/L/main.bean") == b"2026-01-01 open Assets:Cash\n"


def test_a_linked_file_that_is_not_read_only_is_removed_as_a_name() -> None:
    """Only the clear is refused: a hard link with no read-only attribute is removed as any entry (its other name and
    bytes untouched), as before."""
    fs = world(windows=True)
    assert isinstance(job(fs), pb.Published)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/main.bean", b"x")
    fs.outside_link("/L/main.bean", f"{MIRROR}/stray.bean")
    assert isinstance(job(fs), pb.Published)
    assert "stray.bean" not in fs.names_in(MIRROR)
    assert fs.file("/L/main.bean") == b"x"
