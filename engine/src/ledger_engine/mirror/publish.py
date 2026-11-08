"""The mirror's generation publish and its cleanup (`design/specs/mirror/publish.md`; ADR-0015 and its amendments of
2026-10-25, 2026-11-07 and 2026-11-08; S0087, BL-0113). Checked by the TLA+ model `formal/tla/mirror-publish/`
(durability only, EP-19); the functions that carry out its actions are marked `@tla_action(..., model=
"mirror-publish")` and `MAPPING.md` §2.1 names them.

**The folder is ours** (owner, 2026-11-07, "Whole folder is ours"): `<app cache>/workspaces/<key>/mirror/`. Every step
works through one handle on it, opened relative to its parent without following a link (`DirHandle.open_child`), so
no step reads, writes or removes anything outside it: a link at `mirror/` is removed as a link (`open_folder`), a link
inside is removed as a link, a folder inside is removed through handles that follow no link (`clean`). The folders
above `mirror/` are the app's and may be links (§2).

**One job at a time** (ADR-0064): only an engine job (`open` or `rebuild`) holding the engine lock runs the publish
and the cleanup, so no two run together and none runs beside a save.

The steps of a job (§4): `open_folder` and `clean` first (step 1, before the job's load); after the load, `publish`:
the name (`new_name`, step 2), the build into it (`Store.build`, step 3, with the identity check), never an
incomplete build (step 4), the file synced (step 5), the folder synced (step 6), the caller's re-check (step 7: the
ledger set observed again, no `.open` journal), the swap (`swap`, step 8). A generation is never written after it is
published and a name is never used twice (the random tag, §3), so readers that pinned a name read only that build.

Readers resolve `current.db` once and open the generation (§7): the save's gate reads it with `read_published`, the
sidecar with `app.mirror.files.pin`. `fresh` and `fresh_on_disk` are §8's freshness check and its gate part.

Everything here goes through the file-system port (`ledger_engine.fs.port`): the in-memory fake's crash, power-loss and
I/O-error sweeps test it (`tests/mirror/test_publish_crash.py`); the SQLite build and `meta` read are the store's
(`ledger_engine.mirror.store`).
"""

from __future__ import annotations

import errno
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import DirHandle, FileSystem, Kind, check_name, split_absolute
from ledger_engine.ledger.beancount_port import ParseResult
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.ledger_set import Fingerprint, UndecodableError, decode, digest, encode
from ledger_engine.mirror.build import BuildFailed, Built, Stamp
from ledger_engine.mirror.core import schema
from ledger_engine.tla import tla_action

CURRENT: Final = "current.db"
WORKSPACES: Final = "workspaces"
FOLDER: Final = "mirror"
FOLDER_MODE: Final = 0o700
GRACE_NS: Final = 10 * 60 * 10**9
"""§6: a replaced generation is kept 10 minutes (ADR-0015 amendment of 2026-10-25; a configuration value)."""
EXTRA: Final = 2
"""§6: at most 2 generations besides the current and the previous one (owner, 2026-11-08, "At most 2 extra")."""
MAX_DEPTH: Final = 32
"""How deep the cleanup goes into a folder someone left inside; deeper is a failed removal, tried again next time."""
MAX_NUMBER: Final = 999_999_999_999
"""The largest `N` (12 digits; the rebuild's `floor` has the same bound): naming and recognition agree on it (round 1,
verifier V2). Only a name another program made can bring the folder near it; `new_name` then stays at it."""
GEN_NAME: Final = re.compile(r"gen-([1-9][0-9]{0,11})-([0-9a-f]{16})\.db")
JOURNALS: Final = ("-journal", "-wal", "-shm")
"""The names SQLite gives the files beside a database (a rollback journal; a WAL and its index, which our builds never
make but an outside writer could)."""

type Recheck = Literal["same", "moved", "paused"]
type Observe = Callable[[Fingerprint], Fingerprint]
"""Observe the ledger set a fingerprint names, now (`ledger_engine.ledger.fingerprint.observe`)."""
type NotPublishedWhy = Literal["inexact", "replaced", "incomplete", "moved", "paused"]
type PublishedBy = Literal["open", "rebuild"]
"""§8: the job that published a generation. The open stamps exactly the bytes its overlay load parsed, every `include`
pattern included; the rebuild stamps what its real load read, which can miss a pattern (§11 "the stamp's globs"). So
only a generation an open published lets a later open skip its load (S0088 round 1; owner, 2026-11-08)."""


@dataclass(frozen=True, slots=True)
class Gen:
    """A generation: its number `N` and its name `gen-<N>-<tag>.db` (§3)."""

    number: int
    name: str


def gen_of(name: str) -> Gen | None:
    m = GEN_NAME.fullmatch(name)
    return None if m is None else Gen(int(m[1]), name)


def _numbered(name: str) -> int | None:
    """The `N` of a generation's name or of a journal named after one."""
    for suffix in ("", *JOURNALS):
        if name.endswith(suffix) and (g := gen_of(name[: len(name) - len(suffix)])) is not None:
            return g.number
    return None


@dataclass(frozen=True, slots=True)
class Meta:
    """A generation's `meta`, as far as the publish, the cleanup and the gate read it (§8)."""

    ledger_version: str
    fingerprint: str
    export_version: str
    engine_build: str
    as_of: str
    build_complete: bool
    published_by: PublishedBy


META_KEYS: Final = (
    "ledger_version",
    "fingerprint",
    "export_version",
    "engine_build",
    "as_of",
    "build_complete",
    "published_by",
)


def meta_of(facts: Mapping[str, object]) -> Meta | None:
    """The `Meta` of a generation's key-value facts; None when a key is missing or not text, or `build_complete` is
    not `0` or `1` (a file that is not a whole mirror of this kind)."""
    got: dict[str, str] = {}
    for key in META_KEYS:
        value = facts.get(key)
        if not isinstance(value, str) or not value:
            return None
        got[key] = value
    if got["build_complete"] not in ("0", "1"):
        return None
    by = got["published_by"]
    if by != "open" and by != "rebuild":
        return None
    return Meta(
        ledger_version=got["ledger_version"],
        fingerprint=got["fingerprint"],
        export_version=got["export_version"],
        engine_build=got["engine_build"],
        as_of=got["as_of"],
        build_complete=got["build_complete"] == "1",
        published_by=by,
    )


@dataclass(frozen=True, slots=True)
class Replaced:
    """After the build, the entry at the generation's name was not the file the create made (§2): not published."""


class Store(Protocol):
    """The build of a generation into a name of the folder, and the read of a generation's `meta` (the real one:
    `ledger_engine.mirror.store.SqliteStore`; over the in-memory fake: `tests/mirror/fake_store.py`)."""

    def build(
        self, d: DirHandle, name: str, result: ParseResult, today: date, stamp: Stamp
    ) -> Built | BuildFailed | Replaced:
        """Create `name` exclusively through `d`, write the whole mirror into it in one transaction (its journal
        removed at the commit), and check that the entry is still the file the create made. A build that fails removes
        what it made, through `d`, and raises."""
        ...

    def meta(self, d: DirHandle, name: str) -> Meta | None:
        """The `meta` of the generation `name`, read only; None when there is no such regular file, a hot journal
        stands beside it (SQLite refuses it read-only), or it is not a whole mirror."""
        ...


# -- §2: the folder ---------------------------------------------------------------------------------------------------


def _join(*parts: str) -> str:
    return "/".join([parts[0].rstrip("/"), *parts[1:]]) or "/"


def _parent_path(app_cache: str, key: str) -> str:
    split_absolute(app_cache)
    check_name(key)
    return _join(app_cache, WORKSPACES, key)


def _ensure(fs: FileSystem, folder: str, name: str) -> None:
    """The app's folder `name` in `folder` (following links: the app's own folders), made if missing."""
    with fs.open_dir(folder) as h:
        if h.stat(name) is None:
            h.mkdir(name, FOLDER_MODE)
            h.sync()


def open_folder(fs: FileSystem, app_cache: str, key: str) -> DirHandle:
    """§2 "The folder itself", first in every job (the app's folders above it made if missing, following links: they
    are the app's): a link at `mirror/` is removed as a link, a file there is removed,
    the folder is made if missing (mode 0700) and its parent synced; then one handle on it, never through a link (if a
    link took its place meanwhile, the open fails and the job ends: §11 "Limit (link swapped in)")."""
    parent_path = _parent_path(app_cache, key)
    above, _, name = app_cache.rstrip("/").rpartition("/")
    if name:  # the app's cache folder itself may have been purged by the OS (§9): made again
        _ensure(fs, above or "/", name)
    _ensure(fs, app_cache, WORKSPACES)
    _ensure(fs, _join(app_cache, WORKSPACES), key)
    with fs.open_dir(parent_path) as parent:
        st = parent.stat(FOLDER)
        if st is not None and st.kind is not Kind.DIR:
            parent.remove(FOLDER)  # a link (never followed), a file or any other entry: ours to remove
            st = None
        if st is None:
            parent.mkdir(FOLDER, FOLDER_MODE)
        parent.sync()
        d = parent.open_child(FOLDER)
        if d.device() != parent.device():  # another volume mounted at `mirror/`: not ours (round 1, red team F1)
            d.close()
            raise OSError(errno.EXDEV, "another volume is mounted at the mirror folder")
        return d


def current(d: DirHandle) -> Gen | None:
    """The current generation (§6 "Kept"): `current.db` is a link whose target is exactly a generation name and that
    entry is a regular file of the folder. Anything else: none."""
    st = d.stat(CURRENT)
    if st is None or st.kind is not Kind.LINK:
        return None
    target = d.readlink(CURRENT)
    gen = None if target is None else gen_of(target)
    if gen is None:
        return None
    gst = d.stat(gen.name)
    return gen if gst is not None and gst.kind is Kind.FILE else None


# -- §6: the cleanup --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cleaned:
    """What the cleanup left: the current generation, the largest `N` in any name left (generations and their journals,
    §3), and how many entries it removed and failed to remove (logged, content-free: INV-23)."""

    current: Gen | None
    top: int
    removed: int
    failed: int


def _journalled(names: set[str], name: str) -> bool:
    return any(f"{name}{suffix}" in names for suffix in JOURNALS)


def _keep(d: DirHandle, store: Store, names: set[str], now_ns: int, grace_ns: int, extra: int) -> set[str]:
    cur = current(d)
    if cur is None:
        return set()
    below: list[tuple[Gen, int]] = []
    for name in names:
        g = gen_of(name)
        if g is None or g.number >= cur.number:
            continue
        st = d.stat(name)
        if st is not None and st.kind is Kind.FILE:
            below.append((g, st.mtime_ns))
    below.sort(key=lambda x: (x[0].number, x[0].name), reverse=True)
    previous = next(
        (g for g, _ in below if not _journalled(names, g.name) and (m := store.meta(d, g.name)) and m.build_complete),
        None,
    )
    young = sorted(
        ((g, t) for g, t in below if g != previous and now_ns - t < grace_ns), key=lambda x: x[1], reverse=True
    )
    gens = {cur.name, *(g.name for g, _ in young[:extra]), *([previous.name] if previous else [])}
    return {CURRENT, *gens, *(f"{g}{s}" for g in gens for s in JOURNALS if f"{g}{s}" in names)}


def _remove_tree(d: DirHandle, name: str, depth: int, dev: int) -> None:
    """Remove the folder `name` and everything in it, through handles that follow no link (a link is removed as a
    link), never crossing into another volume (`dev`: the mirror folder's): what another volume holds is left, so the
    folders above it stay and the removal fails (as `rm -x`; round 1, red team F1)."""
    if depth > MAX_DEPTH:
        raise OSError(errno.ELOOP, "too deep")
    with d.open_child(name) as c:
        if c.device() != dev:
            raise OSError(errno.EXDEV, "another volume")
        for entry in c.names():
            st = c.stat(entry)
            if st is None or st.dev != dev:
                continue  # another volume's mount point: left
            if st.kind is Kind.DIR:
                _remove_tree(c, entry, depth + 1, dev)
            else:
                c.remove(entry)
    d.rmdir(name)


@tla_action("P_CleanDb", "P_CleanJl", "P_CleanDone", model="mirror-publish")
def clean(d: DirHandle, store: Store, now_ns: int, *, grace_ns: int = GRACE_NS, extra: int = EXTRA) -> Cleaned:
    """§6, first in every job. Kept: `current.db` when it names a generation of the folder, the current generation,
    the previous one (the highest-numbered below the current with no journal beside it and `build_complete = 1`), and
    at most `extra` others below the current whose modification time (their replacement time, §4 step 8) is less
    than `grace_ns` before `now_ns`, the newest first; a journal beside a kept generation is left. Everything else
    is removed: generations above the current one (never published), other generations and journals, temporary links,
    `current.db` when it is not a kept link, `current.db-*`, any other file, link or folder. A removal that fails is
    counted and skipped (the next cleanup tries again). Another volume mounted in the folder is never entered or
    removed: counted as failed (round 1, red team F1: "the folder is ours" never meant another volume's files)."""
    names = set(d.names())
    keep = _keep(d, store, names, now_ns, grace_ns, extra)
    dev = d.device()
    removed = failed = 0
    for name in sorted(names - keep):
        try:
            st = d.stat(name)
            if st is None:
                continue
            if st.dev != dev:  # another volume's mount point: never entered or removed (round 1, red team F1)
                failed += 1
                continue
            if st.kind is Kind.DIR:
                _remove_tree(d, name, 0, dev)
            else:
                d.remove(name)
            removed += 1
        except OSError:
            failed += 1
    top = max((n for name in d.names() if (n := _numbered(name)) is not None), default=0)
    return Cleaned(current(d), top, removed, failed)


# -- §3, §4: the name, the build, the swap -----------------------------------------------------------------------------


def new_tag() -> str:
    """64 bits from the operating system's cryptographic random source, as 16 lowercase hexadecimal digits (§3)."""
    return secrets.token_hex(8)


@tla_action("P_Create", model="mirror-publish")
def new_name(top: int, floor: int, tag: str) -> Gen:
    """§3: `N` is 1 + the largest of every number left in the folder (`Cleaned.top`, the current one's included) and
    the sidecar's floor (the highest it published or served this session)."""
    if not re.fullmatch(r"[0-9a-f]{16}", tag):
        msg = "tag: not 16 lowercase hexadecimal digits"
        raise ValueError(msg)
    number = min(1 + max(top, floor, 0), MAX_NUMBER)  # at the bound the order is lost, never a name (the tag)
    return Gen(number, f"gen-{number}-{tag}.db")


@dataclass(frozen=True, slots=True)
class Published:
    """The generation is current: `current.db` names it (durable at the next job's folder sync, §4 step 8)."""

    gen: Gen
    ledger_version: str


@dataclass(frozen=True, slots=True)
class NotPublished:
    """Nothing was published (`current.db` unchanged); a file the build made waits for the next cleanup.
    `inexact`: the build refused to round (no file); `replaced`: the entry at the name was not the file the create
    made (§2); `incomplete`: the build left a table out (§4 step 4); `moved`: the ledger moved since the stamp;
    `paused`: an unfinished save's `.open` journal (§4 step 7)."""

    why: NotPublishedWhy


@tla_action("P_Check", "P_SyncFile", "P_DirSync", "P_ReObs", "P_JCheck", model="mirror-publish")
def publish(
    d: DirHandle,
    cleaned: Cleaned,
    store: Store,
    result: ParseResult,
    fingerprint: Fingerprint,
    *,
    floor: int,
    today: date,
    built_at: str,
    engine_build: str,
    recheck: Callable[[], Recheck],
    by: PublishedBy,
    tag: str | None = None,
) -> Published | NotPublished:
    """§4 steps 2 to 8, after the load (`result`, stamped with `fingerprint`: the observation before it, equal to the
    one after it). `recheck` is step 7: the ledger set observed again equals `fingerprint` (else `moved`) and this
    workspace has no `.open` journal (else `paused`); it runs after the syncs, just before the swap."""
    gen = new_name(cleaned.top, floor, tag or new_tag())
    version = f"{gen.number}/{digest(fingerprint)}"
    stamp = Stamp(version, encode(fingerprint).decode("ascii"), built_at, engine_build, by)
    built = store.build(d, gen.name, result, today, stamp)  # step 3
    if isinstance(built, BuildFailed):
        return NotPublished("inexact")
    if isinstance(built, Replaced):
        return NotPublished("replaced")
    if not built.complete:  # step 4: a published generation always says `build_complete = 1`
        return NotPublished("incomplete")
    d.sync_file(gen.name)  # step 5: `F_FULLFSYNC`; SQLite's own syncs do not reach the disk on macOS
    d.sync()  # step 6: availability (the entry before the link; the previous job's rename made durable)
    match recheck():  # step 7
        case "moved":
            return NotPublished("moved")
        case "paused":
            return NotPublished("paused")
        case "same":
            pass
    swap(d, gen)  # step 8
    return Published(gen, version)


@tla_action("P_Swap", model="mirror-publish")
def swap(d: DirHandle, gen: Gen) -> None:
    """§4 step 8: the replaced generation's modification time set to now (its replacement time, §6), then a temporary
    link to the new generation's name renamed over `current.db`: readers see the old link or the new one, never
    neither."""
    old = current(d)
    if old is not None:
        d.touch(old.name)
    tmp = f"{CURRENT}.{gen.name[len(f'gen-{gen.number}-') : -len('.db')]}.tmp"
    st = d.stat(tmp)
    if st is not None:  # ours: whatever stands there goes
        if st.kind is Kind.DIR:
            _remove_tree(d, tmp, 0, d.device())
        else:
            d.remove(tmp)
    d.symlink(gen.name, tmp)
    cur = d.stat(CURRENT)
    if cur is not None and cur.kind is Kind.DIR:
        # a folder the cleanup could not remove (round 1, red team F6): moved aside whole, for the next cleanup, so a
        # link can replace it; renaming a folder needs no right inside it
        d.rename(CURRENT, f"{CURRENT}.{tmp[len(CURRENT) + 1 : -len('.tmp')]}.aside")
    d.rename(tmp, CURRENT)


# -- §7, §8: what the gate reads; freshness ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Current:
    gen: Gen
    meta: Meta


@tla_action("R_Resolve", model="mirror-publish")
def read_published(fs: FileSystem, app_cache: str, key: str, store: Store) -> Current | None:
    """The save's gate's read (§7): `current.db` resolved once, through a handle on `mirror/` that follows no link,
    and that generation's `meta`. It makes, cleans and writes nothing. None when there is no published generation, or
    it cannot be read, has a journal beside it or says it is incomplete (the gate refuses `mirror_stale`)."""
    try:
        with fs.open_dir(_parent_path(app_cache, key)) as parent, parent.open_child(FOLDER) as d:
            gen = current(d)
            facts = None if gen is None else store.meta(d, gen.name)
    except OSError:
        return None
    if gen is None or facts is None or not facts.build_complete:
        return None
    return Current(gen, facts)


def same_ledger(fs: FileSystem, a: str, b: str) -> bool:
    """Whether two root paths name one ledger root: their `path_key`s are equal (links resolved, names folded as the
    volume compares them). A path that cannot be resolved names none."""
    try:
        return core_paths.path_key(fs.resolve(a)) == core_paths.path_key(fs.resolve(b))
    except (OSError, ValueError):
        return False


def fresh_on_disk(meta: Meta, observe: Observe, root_ok: Callable[[str], bool]) -> bool:
    """§7, the gate's check: the generation is of this ledger (`root_ok` of its fingerprint's root: the caller's root,
    compared by `same_ledger`; S0087 round 1, red team F2: a generation of another ledger under this key, or of the
    ledger's old place after it was located elsewhere, is not this ledger's), and the ledger set in its own
    `fingerprint`, observed now, has its digest. Only the ledger's bytes count. An undecodable fingerprint, or one
    that cannot be observed, is not fresh."""
    try:
        fp = decode(meta.fingerprint.encode("ascii"))
        return root_ok(fp.root) and digest(observe(fp)) == digest(fp)
    except (UnicodeEncodeError, UndecodableError, OSError):
        return False


def fresh(meta: Meta, observe: Observe, root_ok: Callable[[str], bool], *, engine_build: str, today: date) -> bool:
    """§8: fresh on disk (this ledger's), built with this engine's DDL and row logic, as of today, and complete."""
    return (
        meta.build_complete
        and meta.export_version == schema.EXPORT_VERSION
        and meta.engine_build == engine_build
        and meta.as_of == today.isoformat()
        and fresh_on_disk(meta, observe, root_ok)
    )
