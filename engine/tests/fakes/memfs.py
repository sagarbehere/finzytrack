"""An in-memory file system with a separate durable layer, for the write path's tests (`ledger_engine.fs.port`;
`design/specs/engine-jobs.md` §13 "File-system assumptions", §14 "Save, durable order"; S0060).

**State.** Inodes: files (bytes, mode, and the bytes their last sync made durable), directories (entries, the entries
their last sync made durable, and the entry operations made since), links (an absolute target). A directory belongs
to a volume whose names compare by NFD and, when the volume is case-insensitive, by `casefold`; the stored spelling
is kept.

**Durability, as §13 assumes and no more:** a file's data is durable after its sync; an entry operation (create,
mkdir, remove, swap, exclusive rename) is durable after its directory's sync, which makes every operation made in that
directory before it durable. `power_loss_states()` gives every state a power loss may leave: each pending entry
operation kept or dropped on its own (applied in order; one whose precondition no longer holds does nothing), a swap
or a rename kept or dropped whole, and each file whose data is not synced at its synced bytes (empty if never synced:
an entry kept before its data), its current bytes, or cut off (the first half of its current bytes).

**Injection.** Every effect of the port's operations is numbered from 0 (`effects` lists their labels):
`create` is three effects (the entry, the write, the data sync), every other write one (`rmdir` included).
`inject(at, ...)` makes effect `at` raise (several faults may wait at once, each fires once: a save's and then its
rollback's, S0061): `Crash` (a `BaseException`, as a process death that no `except Exception` sees) or `OSError`
(`ENOSPC` by default), before the effect, or for a write after writing only its first `cut` bytes, or with
`after=True` after the effect took place (an operation that happened but answered an error). `on_effect(i, label)`
runs before each effect, for an outside program to act between two effects (`outside_*`: durable, as an editor that
syncs its save; they are not effects).

**Resolution, as the macOS adapter does it:** links are followed, a dangling link counts as absent (its own name is
the first missing component), and `Resolved.identity` is `(1, inode)`. Files may have several hard links
(`outside_link`); `stat` gives the link count, and a locked file (`outside_lock`, `uchg`) refuses a swap, a rename
and a remove. `create(..., like=...)` copies `like`'s extended attributes (`outside_setxattr`). With `stores_nfd`
the volume keeps every name in NFD, as HFS+ does (S0061 probe). `MemLocks` stands for the ledger-set locks of one
process (`outside_hold`: another process holds one).
"""

# pyright: reportPrivateUsage=false
# (MemDir is a friend of MemFS: one fake, two classes)
from __future__ import annotations

import errno
import itertools
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Self

import pytest

from ledger_engine.fs.port import (
    EntryStat,
    FoldRule,
    Kind,
    MalformedPathError,
    Resolved,
    Right,
    check_name,
    split_absolute,
)

ROOT = 1
MAX_STATES = 200_000


class Crash(BaseException):
    """The process died at an injected effect."""


def _key(name: str, ci: bool) -> str:
    n = unicodedata.normalize("NFD", name)
    return unicodedata.normalize("NFD", n.casefold()) if ci else n


@dataclass
class _File:
    data: bytes
    synced: bytes
    mode: int
    xattrs: dict[str, bytes] = field(default_factory=lambda: {})
    locked: bool = False
    deny: set[Right] = field(default_factory=lambda: set())


@dataclass
class _Other:
    """A FIFO (any entry that is neither a file, a folder nor a link)."""


@dataclass
class _Link:
    target: str


@dataclass
class _Dir:
    ci: bool
    parent: int
    entries: dict[str, tuple[str, int]] = field(default_factory=lambda: {})  # key -> (stored name, inode)
    durable: dict[str, tuple[str, int]] = field(default_factory=lambda: {})
    pending: list[tuple[str, ...]] = field(default_factory=lambda: [])  # ("link"|"unlink"|"swap"|"rename", ...)
    mode: int = 0o755
    deny: set[Right] = field(default_factory=lambda: set())  # CHANGE_ENTRIES: read-only; DELETE: deny delete_child
    no_swap: bool = False  # a volume without RENAME_SWAP and RENAME_EXCL (exFAT, FAT)


_Node = _File | _Dir | _Link | _Other


@dataclass(frozen=True, slots=True)
class _Fault:
    at: int
    crash: bool
    err: int
    cut: int | None
    after: bool


def _apply(entries: dict[str, tuple[str, int]], op: tuple[str, ...]) -> None:
    """One entry operation on `entries`; it does nothing when its precondition does not hold."""
    kind = op[0]
    if kind == "link":
        _, key, stored, ino = op
        if key not in entries:
            entries[key] = (stored, int(ino))
    elif kind == "unlink":
        entries.pop(op[1], None)
    elif kind == "swap":
        _, ka, kb = op
        if ka in entries and kb in entries:
            (sa, ia), (sb, ib) = entries[ka], entries[kb]
            entries[ka], entries[kb] = (sa, ib), (sb, ia)
    elif kind == "rename":
        _, ks, kd, stored = op
        if ks in entries and kd not in entries:
            entries[kd] = (stored, entries.pop(ks)[1])


class MemFS:
    """The fake (`ledger_engine.fs.port.FileSystem`)."""

    def __init__(self, *, case_insensitive: bool = True, stores_nfd: bool = False) -> None:
        self.nodes: dict[int, _Node] = {ROOT: _Dir(ci=case_insensitive, parent=ROOT)}
        self._next = ROOT + 1
        self.effects: list[str] = []
        self._faults: dict[int, _Fault] = {}
        self.on_effect: Callable[[int, str], None] | None = None
        self.stores_nfd = stores_nfd  # HFS+: every name stored in NFD, whatever spelling it was given (S0061 probe)

    def stored(self, name: str) -> str:
        """The spelling the volume keeps for a name it is given."""
        return unicodedata.normalize("NFD", name) if self.stores_nfd else name

    # -- injection ---------------------------------------------------------------------------------------------
    def inject(
        self, at: int, *, crash: bool = True, err: int = errno.ENOSPC, cut: int | None = None, after: bool = False
    ) -> None:
        self._faults[at] = _Fault(at=at, crash=crash, err=err, cut=cut, after=after)

    def _effect(self, label: str, do: Callable[[], None], write: Callable[[int], None] | None = None) -> None:
        """One numbered effect: `do` carries it out, unless an injected fault comes first (a write first writes its
        `cut` prefix) or after it (`after`)."""
        i = len(self.effects)
        if self.on_effect is not None:
            self.on_effect(i, label)
        self.effects.append(label)
        f = self._faults.pop(i, None)
        if f is None:
            do()
            return
        if f.after:
            do()
        elif write is not None and f.cut is not None:
            write(f.cut)
        if f.crash:
            raise Crash(label)
        raise OSError(f.err, f"injected at {label}")

    # -- tree ------------------------------------------------------------------------------------------------
    def _dir(self, ino: int) -> _Dir:
        node = self.nodes[ino]
        if not isinstance(node, _Dir):
            raise NotADirectoryError(errno.ENOTDIR, "not a directory")
        return node

    def _new(self, node: _Node) -> int:
        ino = self._next
        self._next += 1
        self.nodes[ino] = node
        return ino

    def _find(self, d: _Dir, name: str) -> tuple[str, int] | None:
        return d.entries.get(_key(name, d.ci))

    def _walk(self, path: str, depth: int = 0) -> tuple[list[str], int, list[str], list[bool]]:
        """Resolve `path` as far as it exists, following links: the stored names of the existing part, its inode, the
        rest as written, and per component whether its directory is case-insensitive."""
        todo = list(split_absolute(path))
        names: list[str] = []
        cis: list[bool] = []
        ino = parent = ROOT
        hops = 0
        while todo:
            node = self.nodes[ino]
            if not isinstance(node, _Dir):
                break
            hit = self._find(node, todo[0])
            if hit is None:
                break
            stored, child = hit
            target = self.nodes[child]
            if isinstance(target, _Link):
                hops += 1
                if hops + depth > 40:
                    raise OSError(errno.ELOOP, "too many links")
                if self._walk(target.target, depth + hops)[2]:
                    break  # a dangling link is absent, as `stat` (macOS) sees it: its own name is missing
                todo = list(split_absolute(target.target)) + todo[1:]
                names, cis, ino = [], [], ROOT
                continue
            names.append(stored)
            cis.append(node.ci)
            parent, ino = ino, child
            todo.pop(0)
        last = self.nodes[ino]
        rest_ci = last.ci if isinstance(last, _Dir) else self._dir(parent).ci
        return names, ino, todo, cis + [rest_ci] * len(todo)

    def nlink(self, ino: int) -> int:
        return sum(1 for d in self.nodes.values() if isinstance(d, _Dir) for _, i in d.entries.values() if i == ino)

    def path_of(self, ino: int) -> str:
        """The current path of a directory inode (its entries chain to the root)."""
        parts: list[str] = []
        while ino != ROOT:
            d = self._dir(ino)
            parent = self._dir(d.parent)
            hit = next(((s, i) for s, i in parent.entries.values() if i == ino), None)
            if hit is None:
                raise FileNotFoundError(errno.ENOENT, "directory removed")
            parts.append(hit[0])
            ino = d.parent
        return "/" + "/".join(reversed(parts))

    # -- the port ----------------------------------------------------------------------------------------------
    def resolve(self, path: str) -> Resolved:
        names, ino, rest, cis = self._walk(path)
        rules = tuple(FoldRule(nfd=True, casefold=ci) for ci in cis)
        return Resolved(existing=tuple(names), rest=tuple(rest), rules=rules, identity=(1, ino))

    def open_dir(self, path: str) -> MemDir:
        _, ino, rest, _ = self._walk(path)
        if rest:
            raise FileNotFoundError(errno.ENOENT, "no such directory")
        self._dir(ino)
        return MemDir(self, ino)

    # -- outside programs (durable, not effects) -----------------------------------------------------------------
    def _parent_and_name(self, path: str) -> tuple[_Dir, str]:
        parts = split_absolute(path)
        _, ino, rest, _ = self._walk("/" + "/".join(parts[:-1]))
        if rest:
            raise FileNotFoundError(errno.ENOENT, "no parent")
        return self._dir(ino), parts[-1]

    def outside_mkdirs(self, path: str, *, case_insensitive: bool | None = None, no_swap: bool | None = None) -> None:
        parts = split_absolute(path)
        for i in range(1, len(parts) + 1):
            d, name = self._parent_and_name("/" + "/".join(parts[:i]))
            if self._find(d, name) is None:
                pino = next(k for k, v in self.nodes.items() if v is d)
                ci = d.ci if case_insensitive is None or i < len(parts) else case_insensitive
                ns = d.no_swap if no_swap is None or i < len(parts) else no_swap
                ino = self._new(_Dir(ci=ci, parent=pino, no_swap=ns))
                d.entries[_key(name, d.ci)] = (self.stored(name), ino)
                d.durable = dict(d.entries)
                d.pending = []

    def outside_write(self, path: str, data: bytes, *, mode: int = 0o644, new_inode: bool = False) -> None:
        """An editor's durable save: in place (its data synced), or with `new_inode` as a save by rename (a new file
        under the name, the folder synced); a new entry is synced with its folder."""
        d, name = self._parent_and_name(path)
        hit = self._find(d, name)
        if hit is None or new_inode:
            ino = self._new(_File(data=data, synced=data, mode=mode))
            d.entries[_key(name, d.ci)] = (self.stored(name), ino)
            d.durable = dict(d.entries)
            d.pending = []
            return
        node = self.nodes[hit[1]]
        if not isinstance(node, _File):
            raise IsADirectoryError(errno.EISDIR, "not a file")
        node.data = node.synced = data

    def _at(self, path: str) -> _Node:
        _, ino, rest, _ = self._walk(path)
        if rest:
            raise FileNotFoundError(errno.ENOENT, "no entry")
        return self.nodes[ino]

    def outside_link(self, path: str, new: str) -> None:
        """A second hard link `new` to the file at `path`, durably."""
        _, ino, rest, _ = self._walk(path)
        if rest or not isinstance(self.nodes[ino], _File):
            raise FileNotFoundError(errno.ENOENT, "no file")
        d, name = self._parent_and_name(new)
        if self._find(d, name) is not None:
            raise FileExistsError(errno.EEXIST, "exists")
        d.entries[_key(name, d.ci)] = (name, ino)
        d.durable = dict(d.entries)
        d.pending = []

    def outside_rename_case(self, path: str, spelling: str) -> None:
        """Rename an entry to another spelling of its name (a case-only rename on a case-insensitive volume),
        durably."""
        d, name = self._parent_and_name(path)
        key = _key(name, d.ci)
        assert _key(spelling, d.ci) == key
        d.entries[key] = (spelling, d.entries[key][1])
        d.durable = dict(d.entries)
        d.pending = []

    def outside_lock(self, path: str) -> None:
        """`chflags uchg`: the file cannot be swapped, renamed or removed."""
        node = self._at(path)
        assert isinstance(node, _File)
        node.locked = True

    def outside_setxattr(self, path: str, key: str, value: bytes) -> None:
        node = self._at(path)
        assert isinstance(node, _File)
        node.xattrs[key] = value

    def xattrs(self, path: str) -> dict[str, bytes]:
        node = self._at(path)
        assert isinstance(node, _File)
        return dict(node.xattrs)

    def outside_deny(self, path: str, right: Right) -> None:
        """An ACL or mode that denies `right` on the file or folder at `path` (on a folder, DELETE is delete_child)."""
        node = self._at(path)
        assert isinstance(node, _File | _Dir)
        node.deny.add(right)

    def outside_fifo(self, path: str) -> None:
        d, name = self._parent_and_name(path)
        d.entries[_key(name, d.ci)] = (name, self._new(_Other()))
        d.durable = dict(d.entries)
        d.pending = []

    def outside_symlink(self, path: str, target: str) -> None:
        d, name = self._parent_and_name(path)
        if self._find(d, name) is not None:
            raise FileExistsError(errno.EEXIST, "exists")
        d.entries[_key(name, d.ci)] = (name, self._new(_Link(target=target)))
        d.durable = dict(d.entries)
        d.pending = []

    def outside_move(self, path: str, to: str) -> None:
        """Move an entry to another folder, durably (a folder moved away, so a link can take its place)."""
        d, name = self._parent_and_name(path)
        hit = self._find(d, name)
        if hit is None:
            raise FileNotFoundError(errno.ENOENT, "no entry")
        nd, nname = self._parent_and_name(to)
        if self._find(nd, nname) is not None:
            raise FileExistsError(errno.EEXIST, "exists")
        del d.entries[_key(name, d.ci)]
        nd.entries[_key(nname, nd.ci)] = (nname, hit[1])
        node = self.nodes[hit[1]]
        if isinstance(node, _Dir):
            node.parent = next(k for k, v in self.nodes.items() if v is nd)
        for x in (d, nd):
            x.durable = dict(x.entries)
            x.pending = []

    # -- observation ---------------------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, tuple[object, ...]]:
        """Every path now: ("file", bytes, mode), ("dir",) or ("link", target), by stored names from the root."""
        out: dict[str, tuple[object, ...]] = {}

        def walk(ino: int, prefix: str) -> None:
            d = self._dir(ino)
            for stored, child in sorted(d.entries.values()):
                p = f"{prefix}/{stored}"
                node = self.nodes[child]
                if isinstance(node, _File):
                    out[p] = ("file", node.data, node.mode)
                elif isinstance(node, _Link):
                    out[p] = ("link", node.target)
                elif isinstance(node, _Other):
                    out[p] = ("other",)
                else:
                    out[p] = ("dir",)
                    walk(child, p)

        walk(ROOT, "")
        return out

    def names_in(self, path: str) -> list[str]:
        """The stored names in the folder at `path`, sorted."""
        _, ino, rest, _ = self._walk(path)
        assert not rest
        return sorted(s for s, _ in self._dir(ino).entries.values())

    def file(self, path: str) -> bytes | None:
        """A file's bytes now (following links), or None when absent."""
        _, ino, rest, _ = self._walk(path)
        if rest:
            return None
        node = self.nodes[ino]
        return node.data if isinstance(node, _File) else None

    def power_loss_states(self) -> Iterator[MemFS]:
        """Every state a power loss may leave now (see the module's docstring), each a fresh `MemFS` that is fully
        durable."""
        dirs = [(ino, d) for ino, d in self.nodes.items() if isinstance(d, _Dir)]
        choices = [list(itertools.product((False, True), repeat=len(d.pending))) for _, d in dirs]
        total = 1
        for c in choices:
            total *= len(c)
        if total > MAX_STATES:
            pytest.fail(f"{total} entry outcomes: too many for an exhaustive power loss")
        seen: set[tuple[object, ...]] = set()
        for pick in itertools.product(*choices):
            tables: dict[int, dict[str, tuple[str, int]]] = {}
            for (ino, d), keep in zip(dirs, pick, strict=True):
                t = dict(d.durable)
                for op, k in zip(d.pending, keep, strict=True):
                    if k:
                        _apply(t, op)
                tables[ino] = t
            reach: list[int] = []
            stack = [ROOT]
            while stack:
                ino = stack.pop()
                reach.append(ino)
                stack.extend(i for _, i in tables.get(ino, {}).values() if isinstance(self.nodes[i], _Dir))
            files = sorted({i for ino in reach for _, i in tables[ino].values() if isinstance(self.nodes[i], _File)})
            options: list[list[bytes]] = []
            for i in files:
                f = self.nodes[i]
                assert isinstance(f, _File)
                opts = [f.synced] if f.data == f.synced else [f.synced, f.data, f.data[: len(f.data) // 2]]
                options.append(list(dict.fromkeys(opts)))
            for datas in itertools.product(*options):
                sig = (tuple(sorted((k, tuple(sorted(v.items()))) for k, v in tables.items())), tuple(datas))
                if sig in seen:
                    continue
                seen.add(sig)
                yield self._durable_copy(tables, dict(zip(files, datas, strict=True)), reach)

    def _durable_copy(
        self, tables: dict[int, dict[str, tuple[str, int]]], datas: dict[int, bytes], reach: list[int]
    ) -> MemFS:
        out = MemFS(stores_nfd=self.stores_nfd)
        out.nodes = {}
        out._next = self._next
        for ino in reach:
            d = self.nodes[ino]
            assert isinstance(d, _Dir)
            out.nodes[ino] = _Dir(
                ci=d.ci, parent=ROOT, entries=dict(tables[ino]), durable=dict(tables[ino]), mode=d.mode
            )
        for ino in reach:
            for _, child in tables[ino].values():
                node = self.nodes[child]
                if isinstance(node, _Dir):
                    out_d = out.nodes[child]
                    assert isinstance(out_d, _Dir)
                    out_d.parent = ino
                elif isinstance(node, _File):
                    out.nodes[child] = _File(
                        data=datas[child],
                        synced=datas[child],
                        mode=node.mode,
                        xattrs=dict(node.xattrs),
                        locked=node.locked,
                    )
                elif isinstance(node, _Link):
                    out.nodes[child] = _Link(target=node.target)
                else:
                    out.nodes[child] = _Other()
        return out


class MemDir:
    """A handle on one directory of a `MemFS` (`ledger_engine.fs.port.DirHandle`)."""

    def __init__(self, fs: MemFS, ino: int) -> None:
        self._fs = fs
        self._ino = ino
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    @property
    def _d(self) -> _Dir:
        if self._closed:
            raise MalformedPathError("closed_handle")
        return self._fs._dir(self._ino)

    def _label(self, what: str, *names: str) -> str:
        try:
            where = self._fs.path_of(self._ino)
        except FileNotFoundError:
            where = "<removed>"
        return f"{what} {where}:" + ",".join(names)

    def path(self) -> str:
        return self._fs.path_of(self._ino)

    def _entry(self, name: str) -> tuple[str, int] | None:
        return self._fs._find(self._d, check_name(name))

    def read(self, name: str) -> bytes | None:
        hit = self._entry(name)
        if hit is None:
            return None
        node = self._fs.nodes[hit[1]]
        if isinstance(node, _Link):
            raise OSError(errno.ELOOP, "a link")
        if isinstance(node, _Dir):
            raise IsADirectoryError(errno.EISDIR, "a directory")
        if isinstance(node, _Other):
            raise OSError(errno.EINVAL, "not a regular file")
        return node.data

    def stat(self, name: str) -> EntryStat | None:
        hit = self._entry(name)
        if hit is None:
            return None
        node = self._fs.nodes[hit[1]]
        kind = {_File: Kind.FILE, _Dir: Kind.DIR, _Link: Kind.LINK, _Other: Kind.OTHER}[type(node)]
        mode = node.mode if isinstance(node, _File | _Dir) else 0o777
        locked = isinstance(node, _File) and node.locked
        return EntryStat(kind=kind, dev=1, ino=hit[1], nlink=self._fs.nlink(hit[1]), mode=mode, locked=locked)

    def _unlocked(self, key: str) -> None:
        """The entry `key` may be renamed away or removed: not locked, and its deletion allowed."""
        hit = self._d.entries.get(key)
        if hit is not None:
            node = self._fs.nodes[hit[1]]
            if isinstance(node, _File) and node.locked:
                raise PermissionError(errno.EPERM, "locked")
            if not self._may_delete(node):
                raise PermissionError(errno.EACCES, "delete denied")

    def _may_delete(self, node: _Node) -> bool:
        d = self._d
        own: set[Right] = node.deny if isinstance(node, _File | _Dir) else set()
        return Right.CHANGE_ENTRIES not in d.deny and Right.DELETE not in d.deny and Right.DELETE not in own

    def _writable(self) -> None:
        if Right.CHANGE_ENTRIES in self._d.deny:
            raise PermissionError(errno.EACCES, "folder not writable")

    def _swappable(self) -> None:
        if self._d.no_swap:
            raise OSError(errno.ENOTSUP, "no RENAME_SWAP or RENAME_EXCL on this volume")

    def allowed(self, name: str, right: Right) -> bool:
        d = self._d
        if name == ".":
            return right not in d.deny
        hit = self._entry(name)
        if hit is None:
            return False
        node = self._fs.nodes[hit[1]]
        if right is Right.DELETE:
            return self._may_delete(node)
        own: set[Right] = node.deny if isinstance(node, _File | _Dir) else set()
        return right not in own

    def can_swap(self) -> bool:
        return not self._d.no_swap

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        d = self._d
        key = _key(check_name(name), d.ci)
        source: _File | None = None
        if like is not None:
            hit = self._entry(like)
            node = None if hit is None else self._fs.nodes[hit[1]]
            if not isinstance(node, _File):
                raise OSError(errno.EINVAL, "not a regular file")
            source = node
        self._writable()
        f = _File(data=b"", synced=b"", mode=mode & 0o7777)

        def link() -> None:
            if key in d.entries:
                raise FileExistsError(errno.EEXIST, "exists")
            ino = self._fs._new(f)
            stored = self._fs.stored(name)
            d.entries[key] = (stored, ino)
            d.pending.append(("link", key, stored, str(ino)))

        def write() -> None:
            f.data = data
            if source is not None:
                if Right.READ_XATTRS in source.deny:
                    raise PermissionError(errno.EACCES, "extended attributes not readable")
                f.xattrs = dict(source.xattrs)

        def cut(n: int) -> None:
            f.data = data[:n]

        def fsync() -> None:
            f.synced = f.data

        self._fs._effect(self._label("create", name), link)
        self._fs._effect(self._label("write", name), write, cut)
        self._fs._effect(self._label("fsync", name), fsync)

    def swap(self, a: str, b: str) -> None:
        d = self._d
        ka, kb = _key(check_name(a), d.ci), _key(check_name(b), d.ci)

        def do() -> None:
            self._swappable()
            self._writable()
            if ka not in d.entries or kb not in d.entries:
                raise FileNotFoundError(errno.ENOENT, "no entry")
            self._unlocked(ka)
            self._unlocked(kb)
            _apply(d.entries, ("swap", ka, kb))
            d.pending.append(("swap", ka, kb))

        self._fs._effect(self._label("swap", a, b), do)

    def rename_excl(self, src: str, dst: str) -> None:
        d = self._d
        ks, kd = _key(check_name(src), d.ci), _key(check_name(dst), d.ci)

        def do() -> None:
            self._swappable()
            self._writable()
            if ks not in d.entries:
                raise FileNotFoundError(errno.ENOENT, "no entry")
            if kd in d.entries:
                raise FileExistsError(errno.EEXIST, "exists")
            self._unlocked(ks)
            stored = self._fs.stored(dst)
            _apply(d.entries, ("rename", ks, kd, stored))
            d.pending.append(("rename", ks, kd, stored))

        self._fs._effect(self._label("rename", src, dst), do)

    def remove(self, name: str) -> None:
        d = self._d
        key = _key(check_name(name), d.ci)

        def do() -> None:
            self._writable()
            hit = d.entries.get(key)
            if hit is None:
                raise FileNotFoundError(errno.ENOENT, "no entry")
            if isinstance(self._fs.nodes[hit[1]], _Dir):
                raise IsADirectoryError(errno.EISDIR, "a directory")
            self._unlocked(key)
            del d.entries[key]
            d.pending.append(("unlink", key))

        self._fs._effect(self._label("remove", name), do)

    def mkdir(self, name: str) -> None:
        d = self._d
        key = _key(check_name(name), d.ci)

        def do() -> None:
            self._writable()
            if key in d.entries:
                raise FileExistsError(errno.EEXIST, "exists")
            ino = self._fs._new(_Dir(ci=d.ci, parent=self._ino, no_swap=d.no_swap))
            stored = self._fs.stored(name)
            d.entries[key] = (stored, ino)
            d.pending.append(("link", key, stored, str(ino)))

        self._fs._effect(self._label("mkdir", name), do)

    def rmdir(self, name: str) -> None:
        d = self._d
        key = _key(check_name(name), d.ci)

        def do() -> None:
            self._writable()
            hit = d.entries.get(key)
            if hit is None:
                raise FileNotFoundError(errno.ENOENT, "no entry")
            node = self._fs.nodes[hit[1]]
            if not isinstance(node, _Dir):
                raise NotADirectoryError(errno.ENOTDIR, "not a directory")
            if node.entries:
                raise OSError(errno.ENOTEMPTY, "directory not empty")
            if not self._may_delete(node):
                raise PermissionError(errno.EACCES, "delete denied")
            del d.entries[key]
            d.pending.append(("unlink", key))

        self._fs._effect(self._label("rmdir", name), do)

    def names(self) -> list[str]:
        return sorted(s for s, _ in self._d.entries.values())

    def sync(self) -> None:
        d = self._d

        def do() -> None:
            d.durable = dict(d.entries)
            d.pending = []

        self._fs._effect(self._label("sync"), do)


class MemLock:
    def __init__(self, locks: MemLocks, key: str) -> None:
        self._locks = locks
        self._key: str | None = key

    def release(self) -> None:
        if self._key is not None:
            self._locks.held.discard(self._key)
            self._key = None


class MemLocks:
    """The ledger-set locks (`ledger_engine.fs.port.Locks`) of one process. `outside_hold`: another process holds a
    key's lock (a process outside the engine lock, §8.1 step 1). `taken` lists every key tried, in order."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.outside: set[str] = set()
        self.taken: list[str] = []

    def outside_hold(self, key: str) -> None:
        self.outside.add(key)

    def outside_release(self, key: str) -> None:
        self.outside.discard(key)

    def try_lock(self, key: str) -> MemLock | None:
        self.taken.append(key)
        if key in self.held or key in self.outside:
            return None
        self.held.add(key)
        return MemLock(self, key)
