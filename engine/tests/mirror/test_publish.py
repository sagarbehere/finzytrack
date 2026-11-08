"""The mirror's generation publish on the real file system (`ledger_engine.mirror.publish`, `.store`;
`design/specs/mirror/publish.md` §2 to §8; ADR-0015 and its amendments; S0087, BL-0113), in temporary folders only
(INV-24). Written from the spec's lines:

- §2 "The whole mirror folder is ours ... a link at `mirror/` is removed as a link ... every step works relative to
  that descriptor ... A link inside is removed as a link, never what it points to; a folder inside is removed with
  everything in it ... following no link"; "the entry at its name ... must still be the file the create made";
- §3 "`gen-<N>-<tag>.db` ... `tag` 16 lowercase hexadecimal digits ... The next `N` is 1 + the largest of: every `N`
  in a generation name or a journal name in the folder after the cleanup, the current generation's, and the floor";
- §4 steps 4, 7, 8; §6 the keep set (current, previous, at most 2 others younger than the grace, oldest going first);
  §7 the gate's read; §8 what `meta` carries and when a generation is fresh.
The crash, power-loss and I/O-error sweeps are `test_publish_crash.py`'s (the in-memory fake).
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast, override

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import DirHandle, FileSystem
from ledger_engine.ledger.core.ledger_set import decode, digest, encode
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.mirror import build as build_mod
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.core import rows, schema
from ledger_engine.mirror.store import SqliteStore
from tests.mirror.publish_support import (
    ENGINE_BUILD,
    KEY,
    TODAY,
    current,
    fs_now,
    ledger_set,
    loaded,
    meta,
    mirror_of,
    publish_job,
    write_ledger,
)

pytestmark = [
    pytest.mark.inv("INV-05"),
    pytest.mark.inv("INV-10"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the file-system port is macOS-only until BL-0087"),
]

MINUTE_NS = 60 * 10**9
NAME = re.compile(r"gen-([1-9][0-9]*)-([0-9a-f]{16})\.db")


@pytest.fixture
def fs() -> FileSystem:
    return for_this_os()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return write_ledger(tmp_path / "L")


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    c = tmp_path / "cache"
    c.mkdir()
    return c


def published(out: pb.Published | pb.NotPublished) -> pb.Published:
    assert isinstance(out, pb.Published), out
    return out


def tree(path: Path) -> dict[str, Any]:
    """Every entry under `path`, not followed through links: its kind and bytes (a link: its target)."""
    out: dict[str, Any] = {}
    for folder, dirs, files in os.walk(path):
        for name in dirs + files:
            p = Path(folder) / name
            if p.is_symlink():
                out[str(p)] = ("link", os.readlink(p))
            elif p.is_dir():
                out[str(p)] = ("dir",)
            else:
                out[str(p)] = ("file", p.read_bytes())
    return out


def outside(tmp_path: Path) -> Path:
    """A folder of the user's that the mirror must never touch, with a file and a folder in it."""
    o = tmp_path / "outside"
    (o / "sub").mkdir(parents=True)
    (o / "keep.txt").write_bytes(b"the user's file")
    (o / "sub" / "deep.txt").write_bytes(b"deep")
    return o


# -- §3, §4, §8: one publish ------------------------------------------------------------------------------------------


def test_a_publish_makes_the_folder_builds_a_tagged_generation_and_links_current_to_it(
    fs: FileSystem, cache: Path, root: Path
) -> None:
    out = published(publish_job(fs, cache, root))
    m = mirror_of(cache)
    assert stat.S_IMODE(m.stat().st_mode) == 0o700
    name = current(cache)
    assert name is not None
    assert NAME.fullmatch(name), name  # a relative link to a tagged generation name
    assert out.gen == pb.Gen(1, name)
    assert stat.S_IMODE((m / name).stat().st_mode) == 0o600
    assert sorted(p.name for p in m.iterdir()) == sorted([name, pb.CURRENT])  # no journal, no temporary link
    _, fp = loaded(root)
    facts = meta(m / name)
    assert facts["ledger_version"] == f"1/{digest(fp)}" == out.ledger_version
    assert decode(facts["fingerprint"].encode("ascii")) == fp
    assert facts["build_complete"] == "1"
    assert facts["engine_build"] == ENGINE_BUILD
    assert facts["as_of"] == TODAY.isoformat()
    assert facts["export_version"] == schema.EXPORT_VERSION


def test_numbers_grow_above_the_current_one_and_the_sidecars_floor(fs: FileSystem, cache: Path, root: Path) -> None:
    assert published(publish_job(fs, cache, root)).gen.number == 1
    assert published(publish_job(fs, cache, root)).gen.number == 2
    assert published(publish_job(fs, cache, root, floor=7)).gen.number == 8
    assert published(publish_job(fs, cache, root, floor=3)).gen.number == 9  # the floor never lowers it


def test_a_name_is_never_used_twice_even_after_the_folder_is_purged(fs: FileSystem, cache: Path, root: Path) -> None:
    """§3 (owner, 2026-11-08, "Random tag in name"): after a purge `N` may start again; the tag makes the name new."""
    first = published(publish_job(fs, cache, root)).gen
    for p in mirror_of(cache).iterdir():
        p.unlink()
    mirror_of(cache).rmdir()
    again = published(publish_job(fs, cache, root)).gen
    assert again.number == first.number == 1
    assert again.name != first.name
    tags = {pb.new_tag() for _ in range(200)}
    assert len(tags) == 200
    assert all(re.fullmatch(r"[0-9a-f]{16}", t) for t in tags)


def test_new_name_counts_every_number_left_in_the_folder() -> None:
    assert pb.new_name(0, 0, "a" * 16) == pb.Gen(1, f"gen-1-{'a' * 16}.db")
    assert pb.new_name(5, 2, "b" * 16).number == 6
    assert pb.new_name(5, 9, "b" * 16).number == 10
    with pytest.raises(ValueError, match="tag"):
        pb.new_name(0, 0, "../x")


def test_the_swap_sets_the_replaced_generations_modification_time(fs: FileSystem, cache: Path, root: Path) -> None:
    first = published(publish_job(fs, cache, root)).gen
    old = mirror_of(cache) / first.name
    os.utime(old, ns=(10**9, 10**9))
    before = fs_now(cache)
    published(publish_job(fs, cache, root))
    assert old.stat().st_mtime_ns >= before - 10**9  # set at the swap: its replacement time (§4 step 8)


# -- §4 steps 4 and 7: what is never published ----------------------------------------------------------------------


def test_an_incomplete_build_is_not_published_and_its_file_waits_for_the_cleanup(
    fs: FileSystem, cache: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = published(publish_job(fs, cache, root)).gen
    monkeypatch.setattr(rows, "COMPLETE", False)
    out = publish_job(fs, cache, root)
    assert out == pb.NotPublished("incomplete")
    assert current(cache) == first.name
    left = [p.name for p in mirror_of(cache).iterdir() if NAME.fullmatch(p.name) and p.name != first.name]
    assert len(left) == 1
    monkeypatch.setattr(rows, "COMPLETE", True)
    published(publish_job(fs, cache, root))
    assert not (mirror_of(cache) / left[0]).exists()  # above the current number: never published, removed


@pytest.mark.parametrize("why", ["moved", "paused"])
def test_a_moved_ledger_or_an_open_journal_at_the_recheck_publishes_nothing(
    fs: FileSystem, cache: Path, root: Path, why: pb.Recheck
) -> None:
    first = published(publish_job(fs, cache, root)).gen
    seen: list[str] = []

    def recheck() -> pb.Recheck:
        seen.append(str(current(cache)))
        return why

    assert publish_job(fs, cache, root, recheck=recheck) == pb.NotPublished(why)  # type: ignore[arg-type]
    assert seen == [first.name]  # the recheck ran after the build, before the swap
    assert current(cache) == first.name


def test_a_build_refused_for_rounding_creates_no_file(
    fs: FileSystem, cache: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inexact(*_: object, **__: object) -> object:
        raise rows.InexactError("account_balances", "balance")

    monkeypatch.setattr(rows, "table_rows", inexact)
    assert publish_job(fs, cache, root) == pb.NotPublished("inexact")
    assert sorted(p.name for p in mirror_of(cache).iterdir()) == []


def test_a_build_that_fails_removes_its_file_and_journal_and_raises(
    fs: FileSystem, cache: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = published(publish_job(fs, cache, root)).gen
    monkeypatch.setattr(schema, "statements", lambda: ["CREATE TABLE x (", "nonsense"])
    with pytest.raises(sqlite3.Error):
        publish_job(fs, cache, root)
    assert sorted(p.name for p in mirror_of(cache).iterdir()) == sorted([first.name, pb.CURRENT])


def test_a_generation_replaced_at_its_name_during_the_build_is_not_published(
    fs: FileSystem, cache: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2: "After the build, the entry at its name ... must still be the file the create made"."""
    first = published(publish_job(fs, cache, root)).gen
    real = build_mod.write

    def write_then_replace(path: str, made: Any) -> None:
        real(path, made)
        data = Path(path).read_bytes()
        Path(path).unlink()
        Path(path).write_bytes(data)  # the same bytes, another file: not the one the create made

    monkeypatch.setattr(build_mod, "write", write_then_replace)
    assert publish_job(fs, cache, root) == pb.NotPublished("replaced")
    assert current(cache) == first.name


# -- §6: the cleanup's keep set -----------------------------------------------------------------------------------


def publish_n(fs: FileSystem, cache: Path, root: Path, n: int) -> list[pb.Gen]:
    return [published(publish_job(fs, cache, root)).gen for _ in range(n)]


def names(cache: Path) -> set[str]:
    return {p.name for p in mirror_of(cache).iterdir()}


def clean_at(fs: FileSystem, cache: Path, now_ns: int) -> pb.Cleaned:
    with pb.open_folder(fs, str(cache), KEY) as d:
        return pb.clean(d, SqliteStore(), now_ns)


def test_the_cleanup_keeps_current_previous_and_at_most_two_others(fs: FileSystem, cache: Path, root: Path) -> None:
    gens = publish_n(fs, cache, root, 7)
    cleaned = clean_at(fs, cache, fs_now(cache))
    assert cleaned.current == gens[-1]
    # current, previous, and the 2 newest of the others (each young): the oldest go first (owner, 2026-11-08)
    assert names(cache) == {pb.CURRENT, *(g.name for g in gens[-4:])}


def test_after_the_grace_period_only_current_and_previous_stay(fs: FileSystem, cache: Path, root: Path) -> None:
    gens = publish_n(fs, cache, root, 4)
    clean_at(fs, cache, fs_now(cache) + 11 * MINUTE_NS)
    assert names(cache) == {pb.CURRENT, gens[-1].name, gens[-2].name}


def test_the_oldest_others_go_first_by_their_replacement_time(fs: FileSystem, cache: Path, root: Path) -> None:
    gens = publish_n(fs, cache, root, 6)
    m = mirror_of(cache)
    assert names(cache) == {pb.CURRENT, *(g.name for g in gens[1:])}  # the 6th job's cleanup took gens[0]
    now = fs_now(cache)
    # the others now are gens[1..3]; make gens[1] and gens[2] the most recently replaced, gens[3] the oldest
    for g, ago in zip(gens[1:4], (1, 2, 5), strict=True):
        os.utime(m / g.name, ns=(now - ago * MINUTE_NS, now - ago * MINUTE_NS))
    clean_at(fs, cache, now)
    assert names(cache) == {pb.CURRENT, gens[5].name, gens[4].name, gens[1].name, gens[2].name}


def test_the_previous_generation_is_the_highest_below_current_that_is_whole_and_complete(
    fs: FileSystem, cache: Path, root: Path
) -> None:
    gens = publish_n(fs, cache, root, 4)
    m = mirror_of(cache)
    (m / f"{gens[2].name}-journal").write_bytes(b"hot")  # a stopped outside writer's journal: not whole
    clean_at(fs, cache, fs_now(cache) + 11 * MINUTE_NS)
    assert names(cache) == {pb.CURRENT, gens[3].name, gens[1].name}  # gens[2] and its journal went: not previous


def test_a_journal_beside_a_kept_generation_is_left(fs: FileSystem, cache: Path, root: Path) -> None:
    gens = publish_n(fs, cache, root, 2)
    m = mirror_of(cache)
    (m / f"{gens[1].name}-journal").write_bytes(b"hot")
    clean_at(fs, cache, fs_now(cache))
    assert f"{gens[1].name}-journal" in names(cache)


def test_what_the_rules_do_not_name_is_removed(fs: FileSystem, cache: Path, root: Path) -> None:
    gens = publish_n(fs, cache, root, 1)
    m = mirror_of(cache)
    above = f"gen-9-{'f' * 16}.db"
    for junk in (above, f"{above}-journal", "current.db-wal", "current.db-journal", "current.db.abc.tmp", "x.txt"):
        (m / junk).write_bytes(b"x")
    (m / "gen-x.db").write_bytes(b"x")
    (m / "folder" / "inner").mkdir(parents=True)
    cleaned = clean_at(fs, cache, fs_now(cache))
    assert names(cache) == {pb.CURRENT, gens[0].name}
    assert cleaned.failed == 0
    assert cleaned.removed == 8
    assert cleaned.top == 1


# -- §2: the folder is ours, links are never followed (must-not-pass) --------------------------------------------------


def test_a_link_at_mirror_is_removed_as_a_link_and_its_target_is_untouched(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    o = outside(tmp_path)
    (o / f"gen-1-{'0' * 16}.db").write_bytes(b"looks like ours")
    (o / pb.CURRENT).symlink_to(f"gen-1-{'0' * 16}.db")
    before = tree(o)
    m = mirror_of(cache)
    m.parent.mkdir(parents=True)
    m.symlink_to(o)
    published(publish_job(fs, cache, root))
    assert tree(o) == before
    assert m.is_dir()
    assert not m.is_symlink()


def test_a_file_at_mirror_is_replaced_by_the_folder(fs: FileSystem, cache: Path, root: Path) -> None:
    m = mirror_of(cache)
    m.parent.mkdir(parents=True)
    m.write_bytes(b"x")
    published(publish_job(fs, cache, root))
    assert m.is_dir()


def test_links_inside_are_removed_as_links_and_their_targets_are_untouched(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    gens = publish_n(fs, cache, root, 1)
    o = outside(tmp_path)
    (o / f"gen-5-{'1' * 16}.db").write_bytes(b"x")
    before = tree(o)
    m = mirror_of(cache)
    (m / "to-folder").symlink_to(o)
    (m / "to-file").symlink_to(o / "keep.txt")
    (m / f"gen-0-{'2' * 16}.db").symlink_to(o / "keep.txt")
    (m / f"gen-1-{'3' * 16}.db").symlink_to(o / f"gen-5-{'1' * 16}.db")  # a link at a generation's name, below current
    (m / "dangling").symlink_to(tmp_path / "nothing")
    clean_at(fs, cache, fs_now(cache))
    assert tree(o) == before
    assert names(cache) == {pb.CURRENT, gens[0].name}


def test_a_folder_inside_is_removed_with_everything_in_it_and_no_link_in_it_is_followed(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    gens = publish_n(fs, cache, root, 1)
    o = outside(tmp_path)
    before = tree(o)
    m = mirror_of(cache)
    (m / "f" / "g" / "h").mkdir(parents=True)
    (m / "f" / "a.txt").write_bytes(b"a")
    (m / "f" / "g" / "lnk").symlink_to(o)
    (m / "f" / "g" / "h" / "lnk2").symlink_to(o / "sub")
    (m / "f" / "g" / "h" / "b.txt").write_bytes(b"b")
    clean_at(fs, cache, fs_now(cache))
    assert tree(o) == before
    assert names(cache) == {pb.CURRENT, gens[0].name}


def test_current_db_pointing_outside_the_folder_is_not_kept_and_its_target_is_untouched(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    gens = publish_n(fs, cache, root, 1)
    o = outside(tmp_path)
    (o / gens[0].name).write_bytes((mirror_of(cache) / gens[0].name).read_bytes())  # a generation-like file outside
    before = tree(o)
    link = mirror_of(cache) / pb.CURRENT
    link.unlink()
    link.symlink_to(o / gens[0].name)  # absolute: not exactly a generation name of this folder
    cleaned = clean_at(fs, cache, fs_now(cache))
    assert cleaned.current is None
    assert tree(o) == before
    assert pb.CURRENT not in names(cache)


@pytest.mark.parametrize("kind", ["file", "folder"])
def test_current_db_a_file_or_a_folder_is_replaced_by_the_link(
    fs: FileSystem, cache: Path, root: Path, kind: str
) -> None:
    m = mirror_of(cache)
    m.mkdir(parents=True)
    if kind == "file":
        (m / pb.CURRENT).write_bytes(b"SQLite format 3\x00 not ours")
    else:
        (m / pb.CURRENT / "inner").mkdir(parents=True)
        (m / pb.CURRENT / "inner" / "x").write_bytes(b"x")
    out = published(publish_job(fs, cache, root))
    assert current(cache) == out.gen.name


def test_mirror_swapped_for_a_link_during_the_job_writes_nothing_behind_the_link(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    """§2 "one descriptor": a program that moves `mirror/` away and puts a link in its place after the job opened the
    folder cannot lead the build, the link or the cleanup behind the link."""
    published(publish_job(fs, cache, root))
    o = outside(tmp_path)
    before = tree(o)
    m = mirror_of(cache)
    away = m.parent / "away"

    def swap_in_a_link(_: DirHandle) -> None:
        m.rename(away)
        m.symlink_to(o)

    out = published(publish_job(fs, cache, root, around=swap_in_a_link))
    assert tree(o) == before
    assert os.readlink(away / pb.CURRENT) == out.gen.name  # the job finished in its own folder
    assert (away / out.gen.name).is_file()


class StalePath(SqliteStore):
    """The build's path taken just before the folder moved: SQLite opens by path, the only step that can."""

    def __init__(self, move: Any) -> None:
        self._move = move

    @override
    def build(self, d: DirHandle, name: str, *args: Any, **kw: Any) -> Any:
        return super().build(cast(DirHandle, _Stale(d, self._move)), name, *args, **kw)


class _Stale:
    def __init__(self, d: DirHandle, move: Any) -> None:
        self._d = d
        self._move = move

    def path(self) -> str:
        p = self._d.path()
        self._move()
        return p

    def __getattr__(self, name: str) -> Any:
        return getattr(self._d, name)


def test_sqlite_never_creates_a_file_behind_a_link_swapped_in_before_its_open(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path
) -> None:
    """§2 "The build's own open": `mode=rw` never creates a file, so the stale path through the link fails, nothing
    is written behind it, and the created file is removed through the descriptor."""
    published(publish_job(fs, cache, root))
    o = outside(tmp_path)
    before = tree(o)
    m = mirror_of(cache)
    away = m.parent / "away"

    def move() -> None:
        m.rename(away)
        m.symlink_to(o)

    with pytest.raises(sqlite3.Error):
        publish_job(fs, cache, root, store=StalePath(move))
    assert tree(o) == before
    assert len([p for p in away.iterdir() if NAME.fullmatch(p.name)]) == 1  # only the published one


# -- §7: the gate's read, §8 freshness ---------------------------------------------------------------------------------


def test_read_published_reads_the_current_generations_meta(fs: FileSystem, cache: Path, root: Path) -> None:
    out = published(publish_job(fs, cache, root))
    got = pb.read_published(fs, str(cache), KEY, SqliteStore())
    assert got is not None
    assert got.gen == out.gen
    assert got.meta.ledger_version == out.ledger_version
    assert got.meta.build_complete


def test_read_published_never_makes_or_cleans_anything(fs: FileSystem, cache: Path, root: Path) -> None:
    assert pb.read_published(fs, str(cache), KEY, SqliteStore()) is None
    assert sorted(p.name for p in cache.iterdir()) == []
    published(publish_job(fs, cache, root))
    (mirror_of(cache) / "junk").write_bytes(b"x")
    pb.read_published(fs, str(cache), KEY, SqliteStore())
    assert (mirror_of(cache) / "junk").exists()


@pytest.mark.parametrize("damage", ["no_link", "link_to_missing", "garbage", "journal", "link_at_mirror", "absolute"])
def test_read_published_is_none_for_anything_but_a_whole_published_generation(
    fs: FileSystem, cache: Path, root: Path, tmp_path: Path, damage: str
) -> None:
    out = published(publish_job(fs, cache, root))
    m = mirror_of(cache)
    gen = m / out.gen.name
    if damage == "no_link":
        (m / pb.CURRENT).unlink()
    elif damage == "link_to_missing":
        gen.unlink()
    elif damage == "garbage":
        gen.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    elif damage == "journal":
        (m / f"{out.gen.name}-journal").write_bytes(b"hot")
    elif damage == "absolute":
        (m / pb.CURRENT).unlink()
        (m / pb.CURRENT).symlink_to(gen)
    else:
        m.rename(m.parent / "away")
        m.symlink_to(m.parent / "away")  # a whole generation behind a link at `mirror/`: never followed
    assert pb.read_published(fs, str(cache), KEY, SqliteStore()) is None


def test_fresh_needs_the_disk_the_engine_build_the_ddl_and_today(fs: FileSystem, cache: Path, root: Path) -> None:
    published(publish_job(fs, cache, root))
    got = pb.read_published(fs, str(cache), KEY, SqliteStore())
    assert got is not None
    m = got.meta

    def obs(fp: Any) -> Any:
        return observe(fp.ledger_set)

    def ours(r: str) -> bool:
        return pb.same_ledger(fs, r, str(root))

    def theirs(r: str) -> bool:
        return pb.same_ledger(fs, r, str(root.parent / "other.bean"))

    assert not pb.fresh_on_disk(m, obs, theirs)  # another ledger's generation (round 1, red team F2)
    assert not pb.fresh(m, obs, theirs, engine_build=ENGINE_BUILD, today=TODAY)

    assert pb.fresh(m, obs, ours, engine_build=ENGINE_BUILD, today=TODAY)
    assert pb.fresh_on_disk(m, obs, ours)
    assert not pb.fresh(m, obs, ours, engine_build="another build", today=TODAY)
    assert not pb.fresh(m, obs, ours, engine_build=ENGINE_BUILD, today=TODAY.replace(day=9))
    assert not pb.fresh(replace(m, export_version="sha256:other"), obs, ours, engine_build=ENGINE_BUILD, today=TODAY)
    (root.parent / "inc.bean").write_bytes(b"; edited\n")
    assert not pb.fresh_on_disk(m, obs, ours)
    assert not pb.fresh(m, obs, ours, engine_build=ENGINE_BUILD, today=TODAY)
    assert not pb.fresh_on_disk(replace(m, fingerprint="not a fingerprint"), obs, ours)
    assert encode(observe(ledger_set(root)))  # the set it observes is the generation's own


# -- the swap under concurrent reads (BL-0005's acceptance: 0 errors) --------------------------------------------------


def test_readers_during_continuous_publishes_read_only_whole_generations(
    fs: FileSystem, cache: Path, root: Path
) -> None:
    """ADR-0015 "A measured 38,800 reads during 5 s of continuous swaps gave 0 errors": here each read resolves
    `current.db` once (§7) and reads `meta` twice from the generation it pinned. A pinned name the cleanup removed in
    between ("try again", §7) is counted apart; a wrong read (incomplete, two generations, not a mirror) is an error."""
    published(publish_job(fs, cache, root))
    m = mirror_of(cache)
    stop = threading.Event()
    counts = {"reads": 0, "gone": 0}
    errors: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                name = os.readlink(m / pb.CURRENT)
            except OSError as e:
                errors.append(f"no current.db: {e}")
                continue
            if not NAME.fullmatch(name):
                errors.append(f"current.db names {name}")
                continue
            try:
                con = sqlite3.connect(f"{(m / name).as_uri()}?mode=ro", uri=True)
            except sqlite3.OperationalError:
                counts["gone"] += 1
                continue
            try:
                first = dict(con.execute("SELECT key, value FROM m_meta").fetchall())
                second = dict(con.execute("SELECT key, value FROM m_meta").fetchall())
            except sqlite3.OperationalError as e:
                if "unable to open" in str(e):
                    counts["gone"] += 1
                    continue
                errors.append(f"read: {e}")
                continue
            finally:
                con.close()
            if first != second or first.get("build_complete") != "1":
                errors.append("a read that is not one whole generation")
            elif not first["ledger_version"].startswith(f"{NAME.fullmatch(name)[1]}/"):  # type: ignore[index]
                errors.append("meta names another generation")
            counts["reads"] += 1

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    swaps = 0
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline:
            published(publish_job(fs, cache, root))
            swaps += 1
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert errors == []
    assert swaps >= 5
    assert counts["reads"] > 100


def test_a_cache_folder_the_os_purged_is_made_again(fs: FileSystem, cache: Path, root: Path) -> None:
    """§9 "The OS purges the cache": the next job makes the folders and builds under a new name."""
    published(publish_job(fs, cache, root))
    for folder, dirs, files in os.walk(cache, topdown=False):
        for f in files:
            (Path(folder) / f).unlink()
        for d in dirs:
            p = Path(folder) / d
            p.unlink() if p.is_symlink() else p.rmdir()
    cache.rmdir()
    out = published(publish_job(fs, cache, root, now_ns=0))  # now_ns given: the clock probe needs the folder
    assert out.gen.number == 1
    assert current(cache) == out.gen.name


def test_a_journal_sqlite_does_not_find_hot_does_not_hide_a_whole_generation(
    fs: FileSystem, cache: Path, root: Path
) -> None:
    """The gate reads as every reader does: SQLite refuses a hot journal itself (the damage case above), and an
    empty one beside a whole file is a finished transaction's (S0087's mutant `store-meta-ignores-journal`)."""
    out = published(publish_job(fs, cache, root))
    (mirror_of(cache) / f"{out.gen.name}-journal").write_bytes(b"")
    got = pb.read_published(fs, str(cache), KEY, SqliteStore())
    assert got is not None
    assert got.gen == out.gen


def test_same_ledger_compares_roots_as_the_volume_does(fs: FileSystem, root: Path, tmp_path: Path) -> None:
    link = tmp_path / "link.bean"
    link.symlink_to(root)
    assert pb.same_ledger(fs, str(root), str(root))
    assert pb.same_ledger(fs, str(link), str(root))  # a link to the root names the same ledger
    assert pb.same_ledger(fs, str(root).upper(), str(root)) == (os.path.exists(str(root).upper()))
    assert not pb.same_ledger(fs, str(tmp_path / "other.bean"), str(root))
    assert not pb.same_ledger(fs, "relative", str(root))


# -- round 1 (S0087): the review's cases ------------------------------------------------------------------------------


def test_naming_and_recognition_agree_on_the_largest_number(fs: FileSystem, cache: Path, root: Path) -> None:
    """Verifier V2: `N` has at most 12 digits; a name with more is not a generation, and the next name never has
    more."""
    assert pb.new_name(pb.MAX_NUMBER, 0, "a" * 16).number == pb.MAX_NUMBER
    assert pb.gen_of(pb.new_name(pb.MAX_NUMBER, pb.MAX_NUMBER, "a" * 16).name) is not None
    assert pb.gen_of(f"gen-{pb.MAX_NUMBER + 1}-{'a' * 16}.db") is None
    first = published(publish_job(fs, cache, root)).gen
    m = mirror_of(cache)
    big = f"gen-{pb.MAX_NUMBER}-{'b' * 16}.db"
    (m / big).write_bytes((m / first.name).read_bytes())
    (m / pb.CURRENT).unlink()
    (m / pb.CURRENT).symlink_to(big)  # another program made the current one the largest number
    out = published(publish_job(fs, cache, root))
    assert out.gen.number == pb.MAX_NUMBER
    assert current(cache) == out.gen.name != big


def test_a_folder_at_current_db_the_cleanup_cannot_remove_is_moved_aside(
    fs: FileSystem, cache: Path, root: Path
) -> None:
    """Red team F6: such a folder would stop every publish; the swap moves it aside whole for the next cleanup."""
    m = mirror_of(cache)
    stuck = m / pb.CURRENT / "inner"
    stuck.mkdir(parents=True)
    (stuck / "x").write_bytes(b"x")
    stuck.chmod(0o500)  # its entries cannot be removed
    try:
        out = published(publish_job(fs, cache, root))
        assert current(cache) == out.gen.name
        aside = [p for p in m.iterdir() if p.name.endswith(".aside")]
        assert len(aside) == 1
        assert (aside[0] / "inner" / "x").read_bytes() == b"x"
    finally:
        for p in m.rglob("inner"):
            p.chmod(0o700)


def test_an_incomplete_generation_below_current_is_not_previous(fs: FileSystem, cache: Path, root: Path) -> None:
    """Verifier V4 (`prev_ignores_complete`): previous is complete as well as whole."""
    gens = publish_n(fs, cache, root, 3)
    con = sqlite3.connect(mirror_of(cache) / gens[1].name)
    con.execute("UPDATE m_meta SET value = '0' WHERE key = 'build_complete'")
    con.commit()
    con.close()
    clean_at(fs, cache, fs_now(cache) + 11 * MINUTE_NS)
    assert names(cache) == {pb.CURRENT, gens[2].name, gens[0].name}


def test_a_generation_with_any_journal_beside_it_is_not_previous(fs: FileSystem, cache: Path, root: Path) -> None:
    """Verifier V4 (`prev_ignores_journal`): §1 "no journal beside it", even one SQLite does not find hot."""
    gens = publish_n(fs, cache, root, 3)
    (mirror_of(cache) / f"{gens[1].name}-journal").write_bytes(b"")
    clean_at(fs, cache, fs_now(cache) + 11 * MINUTE_NS)
    assert names(cache) == {pb.CURRENT, gens[2].name, gens[0].name}


def hdiutil(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/usr/bin/hdiutil", *args], capture_output=True, text=True, check=False)  # noqa: S603


@pytest.fixture
def mounted(tmp_path: Path) -> Any:
    """A small disk image mounted at a folder (macOS `hdiutil`, no root needed); skipped where it cannot be made."""
    image = tmp_path / "vol.dmg"
    made = hdiutil("create", "-size", "4m", "-fs", "HFS+", "-volname", "ftvol", "-quiet", str(image))
    if made.returncode != 0:
        pytest.skip(f"hdiutil cannot make a disk image here: {made.stderr.strip()[:120]}")
    points: list[Path] = []

    def mount_at(point: Path) -> Path:
        point.mkdir(parents=True, exist_ok=True)
        out = hdiutil("attach", "-nobrowse", "-quiet", "-mountpoint", str(point), str(image))
        if out.returncode != 0:
            pytest.skip(f"hdiutil cannot mount here: {out.stderr.strip()[:120]}")
        points.append(point)
        (point / "volume-file.txt").write_bytes(b"another volume's file")
        return point

    yield mount_at
    for p in points:
        hdiutil("detach", "-force", "-quiet", str(p))


def test_the_cleanup_never_enters_or_removes_another_volume_mounted_inside(
    fs: FileSystem, cache: Path, root: Path, mounted: Any
) -> None:
    """Red team F1: `open_child` with O_NOFOLLOW still enters a mount point; the cleanup compares devices instead."""
    gens = publish_n(fs, cache, root, 1)
    point = mounted(mirror_of(cache) / "mnt")
    cleaned = clean_at(fs, cache, fs_now(cache))
    assert (point / "volume-file.txt").read_bytes() == b"another volume's file"
    assert cleaned.failed == 1
    assert names(cache) == {pb.CURRENT, gens[0].name, "mnt"}


def test_a_volume_mounted_deeper_in_a_folder_is_left_and_the_folder_stays(
    fs: FileSystem, cache: Path, root: Path, mounted: Any
) -> None:
    publish_n(fs, cache, root, 1)
    point = mounted(mirror_of(cache) / "junk" / "deep")
    (mirror_of(cache) / "junk" / "own.txt").write_bytes(b"ours")
    cleaned = clean_at(fs, cache, fs_now(cache))
    assert (point / "volume-file.txt").read_bytes() == b"another volume's file"
    assert not (mirror_of(cache) / "junk" / "own.txt").exists()  # what is ours on our volume goes
    assert cleaned.failed == 1  # the folder above the mount point cannot go


def test_no_job_runs_in_a_mirror_folder_that_is_another_volume(
    fs: FileSystem, cache: Path, root: Path, mounted: Any
) -> None:
    point = mounted(mirror_of(cache))
    with pytest.raises(OSError, match="another volume"):
        publish_job(fs, cache, root, now_ns=0)
    assert (point / "volume-file.txt").read_bytes() == b"another volume's file"


@pytest.mark.parametrize("by", ["open", "rebuild", "someone", ""])
def test_meta_names_the_job_that_published_it(by: str) -> None:
    """S0088 round 1 (publish.md §8 `published_by`): only `open` or `rebuild`; anything else is not a whole mirror of
    this kind (mutant `meta-any-publisher`)."""
    facts = {
        "ledger_version": "1/sha256:" + "0" * 64,
        "fingerprint": "{}",
        "export_version": "x",
        "engine_build": "b",
        "as_of": "2026-11-08",
        "build_complete": "1",
        "published_by": by,
    }
    got = pb.meta_of(facts)
    assert (got is not None) == (by in ("open", "rebuild"))
    if got is not None:
        assert got.published_by == by
