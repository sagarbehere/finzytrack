"""A crash, an I/O error or a power loss at every step of a publish job (`ledger_engine.mirror.publish`;
`design/specs/mirror/publish.md` §9; S0087, BL-0113), over the in-memory file system with its durable layer
(`tests/fakes/memfs.py`) and the fake store (`tests/mirror/fake_store.py`). Written from the spec's lines:

- §9 "No step leaves a reader a partial, incomplete or torn generation: `current.db` names a file only after its data
  is durable (step 5 before step 8), and the old link stays on a whole file until the new one replaces it";
- §4 step 6 (availability): after a power loss `current.db` names an existing file with no journal beside it;
- §9 "The next job": whatever a cut job left, the next one removes it and publishes; the folder then holds only what
  §6 keeps.

The oracle reads the folder as a reader does (§7): `current.db` absent, or a link to exactly a generation name whose
file the store accepts as whole and complete (no journal beside it). The sweep runs the job once to count its effects,
then once per effect with the fault there, and checks the state left and every state a power loss may leave from it.
"""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator

import pytest

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, digest
from ledger_engine.mirror import publish as pb
from tests.fakes.memfs import Crash, MemFS
from tests.mirror.fake_store import FakeStore
from tests.mirror.publish_support import BUILT_AT, ENGINE_BUILD, KEY, TODAY

pytestmark = [pytest.mark.inv("INV-05")]

CACHE = "/cache"
MIRROR = f"{CACHE}/workspaces/{KEY}/mirror"
NOW = 10**18
FP = Fingerprint("/L/main.bean", (("/L/main.bean", Content("0" * 64)),), (), (), ())


def world() -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(CACHE)
    return fs


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
        st = d.stat(pb.CURRENT)
        if st is None or (folder_at_current and st.kind is Kind.DIR):
            return None
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
        assert d.readlink(pb.CURRENT) == out.gen.name
    gens = {n for n in left if pb.gen_of(n) is not None}
    assert left == {pb.CURRENT, *gens}, left  # no journal, no temporary link, nothing else
    assert len(gens) <= 4
    assert all(pb.gen_of(n).number <= out.gen.number for n in gens)  # type: ignore[union-attr]
    for s in states(fs):
        seen_by_a_reader(s)


def effects_of(prepare: int, *, with_junk: bool = False) -> int:
    fs, _ = prepared(prepare, with_junk=with_junk)
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


def prepared(prepare: int, *, with_junk: bool) -> tuple[MemFS, list[str]]:
    fs = world()
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
def test_a_death_at_every_effect_never_leaves_a_reader_a_partial_mirror(prepare: int, with_junk: bool) -> None:
    total = effects_of(prepare, with_junk=with_junk)
    assert total > 10
    for i in range(total):
        fs, published = prepared(prepare, with_junk=with_junk)
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
def test_an_io_error_at_every_effect_never_leaves_a_reader_a_partial_mirror(prepare: int, with_junk: bool) -> None:
    total = effects_of(prepare, with_junk=with_junk)
    for i in range(total):
        fs, _ = prepared(prepare, with_junk=with_junk)
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


def test_a_refused_publish_leaves_the_current_generation_and_its_file_for_the_next_cleanup() -> None:
    fs = world()
    first = job(fs)
    assert isinstance(first, pb.Published)
    whys: tuple[pb.Recheck, ...] = ("moved", "paused")
    for why in whys:
        assert job(fs, why) == pb.NotPublished(why)
        assert seen_by_a_reader(fs) == first.gen.name
    assert_clean_after_the_next_job(fs)


def test_a_power_loss_after_a_publish_lands_on_the_new_or_the_previous_generation() -> None:
    fs = world()
    first = job(fs)
    second = job(fs)
    assert isinstance(first, pb.Published)
    assert isinstance(second, pb.Published)
    seen = {seen_by_a_reader(s) for s in fs.power_loss_states()}
    assert seen == {first.gen.name, second.gen.name}  # the rename may be lost; the previous one is whole and kept


# -- round 1 (S0087): the review's cases over the fake ---------------------------------------------------------------


def test_another_volume_inside_the_folder_is_never_entered_or_removed_in_any_crash() -> None:
    """Red team F1, on the fake: a volume mounted at `mirror/mnt` keeps its file through a death at every effect of
    the next job and every power loss, and the job still publishes."""
    base = world()
    assert isinstance(job(base), pb.Published)
    total = None
    for i in range(64):
        fs = world()
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


def test_a_mirror_folder_on_another_volume_is_refused() -> None:
    fs = world()
    fs.outside_mkdirs(f"{MIRROR}/sub")
    fs.outside_write(f"{MIRROR}/sub/vol.txt", b"another volume's")
    fs.outside_mount(MIRROR, 2)
    with pytest.raises(OSError, match="another volume"):
        job(fs)
    assert fs.file(f"{MIRROR}/sub/vol.txt") == b"another volume's"


def test_a_journal_the_cleanup_cannot_remove_still_raises_the_next_number() -> None:
    """Verifier V5 (`top_ignores_journals`): §3 "every `N` in a generation name or a journal name in the folder after
    the cleanup"."""
    fs = world()
    assert isinstance(job(fs), pb.Published)
    stuck = f"{MIRROR}/gen-9-{'e' * 16}.db-journal"
    fs.outside_write(stuck, b"x")
    fs.outside_lock(stuck)  # its removal fails: it stays
    out = job(fs)
    assert isinstance(out, pb.Published)
    assert out.gen.number == 10


def test_a_folder_at_current_db_that_cannot_be_removed_is_moved_aside_in_every_crash() -> None:
    """Red team F6, on the fake: a locked file inside keeps the folder; the swap moves it aside."""
    for i in range(64):
        fs = world()
        fs.outside_mkdirs(f"{MIRROR}/{pb.CURRENT}")
        fs.outside_write(f"{MIRROR}/{pb.CURRENT}/x", b"x")
        fs.outside_lock(f"{MIRROR}/{pb.CURRENT}/x")
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
