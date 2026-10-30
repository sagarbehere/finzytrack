"""The write-intent journal's record: its fields, its encoding and its names (`design/specs/engine-jobs.md` §6.1
step 8.2, §6.5; ADR-0012 as amended by ADR-0063 and ADR-0064; S0060). Pure.

One save, one name `<session>-<job>`, one record that moves between states by renames in `local/<key>/journal/`:
`.part` (being written; no replace has happened) → `.open` (the save may be half-applied) → `.closed` (saved; R4
owed), and later `.done`, `.mismatch`, `.resolved`, `.kept` (§6.5). The record never changes after it is written:
every state is a rename, so the closed record's fields are written with the journal (post fingerprint, adopted and
parse digests, engine build, workspace registry key and lineage id).

The encoding is canonical JSON with a format name and a version; an older app refuses a newer version (ADR-0012), and
`decode` refuses anything else that is not a whole, well-formed record of this format (a record cut off by a power
loss, a foreign file, a missing or extra field) as `Unreadable`, never a partial record.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from ledger_engine.fs.port import FoldRule, check_name, fold, split_absolute

FORMAT: Final = "finzytrack-save-journal"
VERSION: Final = 1
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_ID: Final = re.compile(r"[A-Za-z0-9_]{1,64}")  # no "-": it joins session and job


class State(StrEnum):
    """The record's suffix (§6.5)."""

    PART = "part"
    OPEN = "open"
    CLOSED = "closed"
    DONE = "done"
    MISMATCH = "mismatch"
    RESOLVED = "resolved"
    KEPT = "kept"


class BadRecordError(ValueError):
    """A record that breaks the format's rules (`what` is content-free)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"bad journal record: {what}")


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A record that is not a whole record of this format and version (`why` is content-free)."""

    why: str


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file the save replaces or creates (§6.1 step 8.2). `pre` is None for a file the save creates (absent
    before). `backup` is the backup's name in the save's backup folder (None for a created file), `temp` the temp's
    name beside the file."""

    canonical_path: str
    path_key: str
    pre: str | None
    post: str
    backup: str | None
    temp: str


@dataclass(frozen=True, slots=True)
class DirEntry:
    """A directory the save creates (§6.1 step 8.3), parents first."""

    canonical_path: str
    path_key: str


@dataclass(frozen=True, slots=True)
class Record:
    session: str
    job: str
    workspace_key: str
    lineage: str
    engine_build: str
    post_fingerprint: str
    adopted_digest: str
    parse_digest: str
    backup_dir: str
    files: tuple[FileEntry, ...]
    created_dirs: tuple[DirEntry, ...]

    @property
    def name(self) -> str:
        return record_name(self.session, self.job)


def check_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise BadRecordError("id")
    return value


def record_name(session: str, job: str) -> str:
    return f"{check_id(session)}-{check_id(job)}"


def file_name(name: str, state: State) -> str:
    """The record's file name in a state: `<session>-<job>.<state>`."""
    return f"{name}.{state.value}"


def parse_file_name(file: str) -> tuple[str, State] | None:
    """`(<session>-<job>, state)` of a record's file name, or None for any other name."""
    base, dot, suffix = file.rpartition(".")
    if not dot:
        return None
    try:
        state = State(suffix)
    except ValueError:
        return None
    session, dash, job = base.partition("-")
    if not dash or not _ID.fullmatch(session) or not _ID.fullmatch(job):
        return None
    return base, state


NAME_MAX: Final = 255  # bytes of one name (APFS, ext4, NTFS in UTF-16 units: at least this)
_RULES: Final = tuple(FoldRule(nfd=n, casefold=c) for n in (False, True) for c in (False, True))


def _short(name: str) -> str:
    """A stand-in for a name too long to carry inside a derived name: 16 hex digits of its SHA-256."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _fits(name: str) -> bool:
    return len(name.encode()) <= NAME_MAX


_TAG: Final = re.compile(r"[A-Za-z0-9_]{1,64}-[A-Za-z0-9_]{1,64}")


def check_tag(tag: str) -> str:
    """A record's name `<session>-<job>`, as every name derived from a save carries it: job numbers restart in each
    session, so a job alone could meet a name a save of an earlier session left (S0061 verifier r2 F1)."""
    if not _TAG.fullmatch(tag):
        raise BadRecordError("tag")
    return tag


def temp_name(name: str, tag: str) -> str:
    """The save's temp beside a file (§6.1 step 8.3): `.<name>.ft-<session>-<job>.tmp`, or `.<sha16>.ft-...tmp` when
    that would pass NAME_MAX (S0060 red team finding 11)."""
    full = f".{name}.ft-{check_tag(tag)}.tmp"
    return full if _fits(full) else f".{_short(name)}.ft-{tag}.tmp"


def staging_name(name: str, tag: str) -> str:
    """Where the save writes a file's new bytes first (§6.1 step 8.3; owner, 2026-10-30: "Staging name
    (Recommended)"): `.<name>.ft-<session>-<job>.new`, synced, then renamed exclusively to the temp name. No swap ever
    touches it, so whatever stands there is a write of ours, cut or torn in any shape, and recovery deletes it.
    `.<sha16>.ft-...new` past NAME_MAX."""
    full = f".{name}.ft-{check_tag(tag)}.new"
    return full if _fits(full) else f".{_short(name)}.ft-{tag}.new"


def restore_temp_name(name: str, tag: str) -> str:
    """The restore temp beside a file (§6.4, §8.1 step 3): `.<name>.ft-restore-<session>-<job>.tmp`, named after the
    save (not the try), so every try (the rollback and each recovery) uses one name per file that the record alone
    gives, and a leftover from a try that was cut off is found without a listing; `.<sha16>.ft-restore-...tmp` past
    NAME_MAX."""
    full = f".{name}.ft-restore-{check_tag(tag)}.tmp"
    return full if _fits(full) else f".{_short(name)}.ft-restore-{tag}.tmp"


def restore_staging_name(name: str, tag: str) -> str:
    """The restore's staging name (§6.4, §8.1 step 3), as `staging_name` is the save's:
    `.<name>.ft-restore-<session>-<job>.new`, written from the backup and synced, then renamed to the restore temp
    name; `.<sha16>.ft-restore-...new` past NAME_MAX."""
    full = f".{name}.ft-restore-{check_tag(tag)}.new"
    return full if _fits(full) else f".{_short(name)}.ft-restore-{tag}.new"


def scratch_names(name: str, tag: str) -> tuple[str, str, str, str]:
    """Every name beside the file `name` that holds bytes of ours only while a save or a try runs: the temp, the
    staging name, the restore temp and the restore staging name (in that order). The save refuses a target at or under
    one and an entry standing at one before its first effect; decode refuses a record naming one as a path."""
    return temp_name(name, tag), staging_name(name, tag), restore_temp_name(name, tag), restore_staging_name(name, tag)


MAX_KEPT: Final = 999  # numbered kept names tried before the save or the try stops without one


def kept_name(name: str, tag: str, n: int = 1) -> str:
    """The visible name beside a file for an outside save that a swap, a removal or a temp check of this save met
    (§6.4; owner, 2026-10-29: "Yes" to the kept-name design, ADR-0013 amendment): `<name>.kept-<session>-<job>`, then
    `...-2` and on when that is taken; `<sha16>.kept-...` past NAME_MAX. Nothing of ours ever deletes a kept name,
    and a journal stops while one exists for any of its files (`is_kept_name`)."""
    if not 1 <= n <= MAX_KEPT:
        raise BadRecordError("kept_number")
    suffix = f".kept-{check_tag(tag)}" + ("" if n == 1 else f"-{n}")
    full = name + suffix
    return full if _fits(full) else _short(name) + suffix


_KEPT_TAIL: Final = re.compile(r"(?:-[0-9]{1,3})?")


def is_kept_name(entry: str, name: str, tag: str) -> bool:
    """`entry` is a kept name of the file `name` for the save `tag` (any number), compared after the widest folding,
    so a volume's own spelling of it still matches (more matches only stop more journals: fail closed)."""
    wide = FoldRule(nfd=True, casefold=True)
    e = fold(entry, wide)
    for stem in (name, _short(name)):
        head = fold(f"{stem}.kept-{tag}", wide)
        if e.startswith(head) and _KEPT_TAIL.fullmatch(e[len(head) :]):
            return True
    return False


def derives_kept_name(entry: str, name: str, tag: str) -> bool:
    """`entry` is exactly one of the kept names this save derives for the file `name` (`kept_name`, any number),
    compared as the volume compares names (after NFD; case exact). The strict form of `is_kept_name`, for the one
    operation that removes a kept copy (the blocked screen's Delete the kept copy, S0062): a wider match there could
    remove a file that is not one of ours, so a spelling the volume did not keep as ours refuses instead."""
    e = unicodedata.normalize("NFD", entry)
    if not is_kept_name(entry, name, tag):
        return False
    return any(e == unicodedata.normalize("NFD", kept_name(name, tag, n)) for n in range(1, MAX_KEPT + 1))


def backup_name(index: int, name: str) -> str:
    """The backup of the file at `index` in the record: `<index>-<name>`, or `<index>-<sha16>` past NAME_MAX."""
    full = f"{index}-{name}"
    return full if _fits(full) else f"{index}-{_short(name)}"


def _parts(path: str) -> tuple[str, ...]:
    """The components of an absolute path with no `.`, `..` or empty component, never the root alone."""
    parts = split_absolute(path)
    if not parts:
        raise BadRecordError("root")
    return parts


def _key_folds(canonical: tuple[str, ...], key: tuple[str, ...]) -> bool:
    """The key folds from the canonical path, component by component, by one of the folding rules (§6.3)."""
    return len(canonical) == len(key) and all(
        any(fold(c, r) == k for r in _RULES) for c, k in zip(canonical, key, strict=True)
    )


def _sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BadRecordError("sha256")
    return value


def _str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BadRecordError("string")
    return value


def _abs(value: object) -> str:
    s = _str(value)
    if not s.startswith("/"):
        raise BadRecordError("absolute")
    return s


def _check(record: Record) -> None:
    """The writer's naming rules (S0060 red team finding 4): recovery acts on these names, so a record that names
    anything else is not a record of ours."""
    check_id(record.session)
    check_id(record.job)
    files: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for i, f in enumerate(record.files):
        canonical, key = _parts(_abs(f.canonical_path)), _parts(_abs(f.path_key))
        if not _key_folds(canonical, key):
            raise BadRecordError("key_not_folded_from_path")
        if f.pre is not None:
            _sha(f.pre)
        _sha(f.post)
        if (f.pre is None) != (f.backup is None):
            raise BadRecordError("backup_iff_pre")
        if f.temp != temp_name(canonical[-1], record.name):
            raise BadRecordError("temp_name")
        if f.backup is not None and f.backup != backup_name(i, canonical[-1]):
            raise BadRecordError("backup_name")
        check_name(f.temp)
        files.append((canonical, key))
    dir_keys: set[str] = set()
    for d in record.created_dirs:
        canonical, key = _parts(_abs(d.canonical_path)), _parts(_abs(d.path_key))
        n = len(canonical)
        if not any(len(fc) > n and fc[:n] == canonical and fk[:n] == key for fc, fk in files):
            raise BadRecordError("created_dir_not_an_ancestor")
        if d.path_key in dir_keys:
            raise BadRecordError("duplicate_dir_key")
        dir_keys.add(d.path_key)
    if not record.files:
        raise BadRecordError("no_files")
    # every temp, staging name and restore temp is its own name, never a file's or a created folder's path, nor inside
    # one (S0060 red team r2 L1; the staging names, S0061 r3);
    # compared folded the widest way, so two names a volume may treat as one count as one
    wide = FoldRule(nfd=True, casefold=True)
    taken = [tuple(fold(c, wide) for c in fc) for fc, _ in files]
    taken += [tuple(fold(c, wide) for c in _parts(d.canonical_path)) for d in record.created_dirs]
    temps = [tuple(fold(c, wide) for c in (*fc[:-1], f.temp)) for (fc, _), f in zip(files, record.files, strict=True)]
    temps += [
        tuple(fold(c, wide) for c in (*fc[:-1], n)) for fc, _ in files for n in scratch_names(fc[-1], record.name)[1:]
    ]
    # no file of the record at a file's kept name, nor under one (a created folder at it is always an ancestor of a
    # file: red team r2 L2): recovery would stop on it for ever
    for fc, _ in files:
        for p in (gc for gc, _ in files):
            if p[: len(fc) - 1] == fc[:-1] and len(p) >= len(fc) and is_kept_name(p[len(fc) - 1], fc[-1], record.name):
                raise BadRecordError("temp_is_a_path")
    if len(set(temps)) != len(temps):
        raise BadRecordError("duplicate_temp")
    if any(p[: len(tp)] == tp for tp in temps for p in taken):
        raise BadRecordError("temp_is_a_path")
    keys = [f.path_key for f in record.files]
    if len(set(keys)) != len(keys):
        raise BadRecordError("duplicate_path_key")
    if _parts(_abs(record.backup_dir))[-1] != record_name(record.session, record.job):
        raise BadRecordError("backup_dir")
    for v in (
        record.workspace_key,
        record.lineage,
        record.engine_build,
        record.post_fingerprint,
        record.adopted_digest,
        record.parse_digest,
    ):
        _str(v)


def encode(record: Record) -> bytes:
    """The record as canonical JSON (sorted keys, no spaces, UTF-8), ending in a newline. `ValueError` for a record
    that breaks the format's rules."""
    _check(record)
    obj = {
        "format": FORMAT,
        "v": VERSION,
        "session": record.session,
        "job": record.job,
        "workspace_key": record.workspace_key,
        "lineage": record.lineage,
        "engine_build": record.engine_build,
        "post_fingerprint": record.post_fingerprint,
        "adopted_digest": record.adopted_digest,
        "parse_digest": record.parse_digest,
        "backup_dir": record.backup_dir,
        "files": [
            {
                "canonical_path": f.canonical_path,
                "path_key": f.path_key,
                "pre": f.pre,
                "post": f.post,
                "backup": f.backup,
                "temp": f.temp,
            }
            for f in record.files
        ],
        "created_dirs": [{"canonical_path": d.canonical_path, "path_key": d.path_key} for d in record.created_dirs],
    }
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


_TOP: Final = frozenset(
    {
        "format",
        "v",
        "session",
        "job",
        "workspace_key",
        "lineage",
        "engine_build",
        "post_fingerprint",
        "adopted_digest",
        "parse_digest",
        "backup_dir",
        "files",
        "created_dirs",
    }
)
_FILE: Final = frozenset({"canonical_path", "path_key", "pre", "post", "backup", "temp"})
_DIR: Final = frozenset({"canonical_path", "path_key"})


def _obj(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BadRecordError("fields")
    d = cast("dict[str, object]", value)
    if frozenset(d) != keys:
        raise BadRecordError("fields")
    return d


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BadRecordError("list")
    return value  # pyright: ignore[reportUnknownVariableType]


def _opt(value: object) -> str | None:
    return None if value is None else _str(value)


def decode(data: bytes) -> Record | Unreadable:
    """The record in `data`, or `Unreadable` (with a content-free reason)."""
    if not data.endswith(b"\n"):
        return Unreadable("cut off")
    try:
        raw: object = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError):
        return Unreadable("not json")
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:  # pyright: ignore[reportUnknownMemberType]
        return Unreadable("not a save journal")
    version: object = raw.get("v")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(version, int) or isinstance(version, bool):
        return Unreadable("no version")
    if version > VERSION:
        return Unreadable("newer version")
    if version != VERSION:
        return Unreadable("unknown version")
    try:
        top = _obj(raw, _TOP)  # pyright: ignore[reportUnknownArgumentType]
        files: list[FileEntry] = []
        for item in _list(top["files"]):
            f = _obj(item, _FILE)
            files.append(
                FileEntry(
                    canonical_path=_str(f["canonical_path"]),
                    path_key=_str(f["path_key"]),
                    pre=_opt(f["pre"]),
                    post=_str(f["post"]),
                    backup=_opt(f["backup"]),
                    temp=_str(f["temp"]),
                )
            )
        dirs = [
            DirEntry(canonical_path=_str(d["canonical_path"]), path_key=_str(d["path_key"]))
            for d in (_obj(item, _DIR) for item in _list(top["created_dirs"]))
        ]
        record = Record(
            session=_str(top["session"]),
            job=_str(top["job"]),
            workspace_key=_str(top["workspace_key"]),
            lineage=_str(top["lineage"]),
            engine_build=_str(top["engine_build"]),
            post_fingerprint=_str(top["post_fingerprint"]),
            adopted_digest=_str(top["adopted_digest"]),
            parse_digest=_str(top["parse_digest"]),
            backup_dir=_str(top["backup_dir"]),
            files=tuple(files),
            created_dirs=tuple(dirs),
        )
        _check(record)
    except ValueError:
        return Unreadable("malformed")
    if encode(record) != data:
        return Unreadable("not canonical")
    return record
