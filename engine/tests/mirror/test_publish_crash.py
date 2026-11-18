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
    fs.outside_lock(stuck)  # its removal fails: it stays
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
    assert kinds[-4:] == ["create", "write", "fsync", "move"], tail


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
