"""The history trim's choice (`design/specs/engine-jobs.md` §6.5: "The last 16 history records of each kind are kept
per workspace; older ones are deleted"; INV-10; BL-0169; S0074). Pure: it reads listings, never the disk.

A save's **history** is everything named after its tag `<session>-<job>` in one key's folders: its journal entries
`<tag>.<suffix>` and its backups folder `<tag>` (the writer names the backups folder after the record,
`journal._check`, in the same key). Names are grouped case-folded (a case-insensitive volume). Records are never
decoded: no field of a record decides what is deleted.

**Kinds** (`HistoryKind`), each keeping its newest `KEEP` on its own: one record state of `.closed`, `.resolved` or
`.kept`; `leftover`, only files that S0066-S0067 development builds wrote (`.done`, `.mismatch`, `.r4-attempt-<n>`;
ADR-0067: not records); `orphan`, a backups folder with no journal entry of its name (a save that stopped before its
journal, or whose `.part` recover-all removed). Newest first by the record's modification time (renames keep it),
an orphan's by its folder's, ties by name; the current save always counts as the newest of its kind.

**Never chosen, and counted in no kind** (fails closed): a save with an `.open` or `.part` entry, or whose tag is in
`protected` (an `.open` or `.part` of that name in any key's journal folder, or the backups folder a readable `.open`
record names); an entry of a suffix not listed above (a newer app's state); two record states; a journal entry that
is not a regular file, a backups entry that is not a folder, two backups entries of one tag, or an entry whose `stat`
failed (`Entry.kind` None). A name that is not `<tag>.<suffix>` (journal) or `<tag>` (backups) is not ours and is
never chosen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core.journal import State

KEEP: Final = 16
"""History records kept per kind and workspace (spec §6.5; the owner's standing number, BL-0169)."""

_TAG: Final = re.compile(r"[A-Za-z0-9_]{1,64}-[A-Za-z0-9_]{1,64}")  # `journal.check_tag`'s shape
_LEFTOVER: Final = re.compile(r"done|mismatch|r4-attempt-[0-9]{1,3}")


class HistoryKind(StrEnum):
    CLOSED = "closed"
    RESOLVED = "resolved"
    KEPT = "kept"
    LEFTOVER = "leftover"
    ORPHAN = "orphan"


_RECORD_KINDS: Final = {
    State.CLOSED.value: HistoryKind.CLOSED,
    State.RESOLVED.value: HistoryKind.RESOLVED,
    State.KEPT.value: HistoryKind.KEPT,
}
_GATING: Final = frozenset({State.OPEN.value, State.PART.value})


@dataclass(frozen=True, slots=True)
class Entry:
    """One entry of a listing as `stat` saw it (not followed through a link): `kind` None when its `stat` failed."""

    name: str
    kind: Kind | None
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class Group:
    """A save's history that may be trimmed: its folded tag, its kind, its place in the order (greater is newer), and
    the stored names to delete: its journal entries and its backups folder (None: it has none)."""

    tag: str
    kind: HistoryKind
    order: tuple[bool, int, str]
    journal: tuple[str, ...]
    backups: str | None


@dataclass
class _Names:
    journal: list[Entry] = field(default_factory=lambda: [])
    backups: list[Entry] = field(default_factory=lambda: [])


def journal_tag(name: str) -> tuple[str, str] | None:
    """`(tag, suffix)` of a journal entry named `<tag>.<suffix>`, or None for a name that is not ours."""
    tag, dot, suffix = name.partition(".")
    if not dot or not suffix or not _TAG.fullmatch(tag):
        return None
    return tag, suffix


def gating(name: str) -> str | None:
    """The folded tag of a journal entry that is an `.open` or `.part` record (it protects its whole name)."""
    parsed = journal_tag(name)
    return parsed[0].casefold() if parsed is not None and parsed[1] in _GATING else None


def _kind(names: _Names) -> HistoryKind | None:
    """The group's kind, or None when it may not be trimmed (the module's docstring)."""
    if any(e.kind is not Kind.FILE for e in names.journal):
        return None
    if len(names.backups) > 1 or any(e.kind is not Kind.DIR for e in names.backups):
        return None
    states: set[HistoryKind] = set()
    for e in names.journal:
        parsed = journal_tag(e.name)
        assert parsed is not None  # noqa: S101 (grouped by it)
        suffix = parsed[1]
        if suffix in _RECORD_KINDS:
            states.add(_RECORD_KINDS[suffix])
        elif not _LEFTOVER.fullmatch(suffix):
            return None  # `.open`, `.part`, or a suffix we do not know
    if len(states) > 1:
        return None
    if states:
        return states.pop()
    return HistoryKind.LEFTOVER if names.journal else HistoryKind.ORPHAN


def _time(names: _Names, kind: HistoryKind) -> int:
    if kind is HistoryKind.ORPHAN:
        return names.backups[0].mtime_ns
    if kind is HistoryKind.LEFTOVER:
        return max(e.mtime_ns for e in names.journal)
    return next(e.mtime_ns for e in names.journal if journal_tag(e.name) is not None and e.name.endswith(f".{kind}"))


def classify(
    journal: Iterable[Entry], backups: Iterable[Entry], protected: frozenset[str], current: str
) -> tuple[Group, ...]:
    """Every save's history in one key's journal and backups listings that the trim may count, the current save's
    included (it is never chosen, `select`)."""
    groups: dict[str, _Names] = {}
    for e in journal:
        parsed = journal_tag(e.name)
        if parsed is not None:
            groups.setdefault(parsed[0].casefold(), _Names()).journal.append(e)
    for e in backups:
        if _TAG.fullmatch(e.name):
            groups.setdefault(e.name.casefold(), _Names()).backups.append(e)
    now = current.casefold()
    out: list[Group] = []
    for tag, names in groups.items():
        if tag in protected:
            continue
        kind = _kind(names)
        if kind is None:
            continue
        out.append(
            Group(
                tag=tag,
                kind=kind,
                order=(tag == now, _time(names, kind), tag),
                journal=tuple(sorted(e.name for e in names.journal)),
                backups=names.backups[0].name if names.backups else None,
            )
        )
    return tuple(out)


def select(
    journal: Iterable[Entry], backups: Iterable[Entry], protected: frozenset[str], current: str, keep: int = KEEP
) -> tuple[Group, ...]:
    """The saves whose history goes: per kind, all but the newest `keep`, never the current save (`current`, as
    `<session>-<job>`) nor anything `classify` leaves out; oldest first."""
    now = current.casefold()
    out: list[Group] = []
    every = classify(journal, backups, protected, current)
    for kind in HistoryKind:
        newest = sorted((g for g in every if g.kind is kind), key=lambda g: g.order, reverse=True)
        out += [g for g in newest[keep:] if g.tag != now]
    return tuple(sorted(out, key=lambda g: g.order))
