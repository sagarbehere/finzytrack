"""What the two jobs that publish the mirror share: the `open` (`ledger_engine.ledger.opening`) and the `rebuild`
(`ledger_engine.ledger.rebuild`) (`design/specs/engine-jobs.md` §7; `design/specs/mirror/publish.md` §4, §8; S0088,
BL-0152).

- `Target`: where and as of when a result is published;
- `unfinished`: this workspace's `.open` journal, looked for before any result is returned (§7: "checks that this
  workspace has no `.open` journal"; a journal folder that cannot be listed counts as one, as the save's step 1);
- `fresh_current`: the published generation, when it is fresh (publish.md §8) and, for the rebuild, its stamp names
  everything the hint names (BL-0212 V1);
- `recheck`: publish.md §4 step 7, just before the swap: the stamped set observed again, and `unfinished` again.

It writes nothing; the publish itself is `ledger_engine.mirror.publish`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from ledger_engine.fs.port import DirHandle, FileSystem
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet, decode
from ledger_engine.ledger.save import records
from ledger_engine.mirror import publish as pb


@dataclass(frozen=True, slots=True)
class Target:
    """Where and as of when the result is published (publish.md §3, §8): the app's cache folder and the workspace's
    registry key (`<app_cache>/workspaces/<key>/mirror/`), the app's machine-local folder and the lineage id (the
    `.open` journal looked for), the sidecar's floor for `N`, the user's civil date (`as_of`, INV-07), the instant
    (`built_at`, and `now_ns` the same instant in nanoseconds since the epoch, which the cleanup's grace period counts
    from: the engine reads no clock, INV-07, EP-10) and this engine's build (`engine_build`)."""

    app_cache: str
    workspace_key: str
    app_local_data: str
    lineage: str
    floor: int
    today: date
    built_at: str
    now_ns: int
    engine_build: str


def unfinished(fs: FileSystem, target: Target) -> bool:
    """Whether this workspace has an unfinished save (an `.open` journal of its key or lineage, `save.records`); a
    journal folder that cannot be listed may hold one: True (fail closed)."""
    try:
        return records(fs, target.app_local_data, target.workspace_key, target.lineage) is not None
    except OSError:
        return True


def _under(file: str, roots: tuple[str, ...]) -> bool:
    return any(file.startswith(root.rstrip("/") + "/") for root in roots)


def within(inner: LedgerSet, outer: LedgerSet) -> bool:
    """Whether `outer` names everything `inner` names (BL-0212 V1): its files, its `include` patterns, its documents
    roots, and its `document` files (one under a documents root of `outer` is named through that root's listing). The
    roots are compared by the caller (`publish.same_ledger`)."""
    return (
        set(inner.files) <= set(outer.files)
        and set(inner.globs) <= set(outer.globs)
        and set(inner.documents) <= set(outer.documents)
        and all(f in outer.document_files or _under(f, outer.documents) for f in inner.document_files)
    )


def fresh_current(
    d: DirHandle,
    store: pb.Store,
    cleaned: pb.Cleaned,
    observe: pb.Observe,
    fs: FileSystem,
    root: str,
    target: Target,
    hint: LedgerSet | None = None,
) -> pb.Current | None:
    """The published generation when it is fresh (publish.md §8: this ledger's, its own set unchanged on disk, this
    engine's build and DDL, today) and, given a `hint` (the rebuild's), its stamp names all the hint names: a hint that
    names a glob or a file the stamp lacks (an outside edit added it before an earlier rebuild, whose hint did not
    know it) means the generation may not describe the disk (BL-0212 V1). Otherwise None: the job loads."""
    if cleaned.current is None:
        return None
    facts = store.meta(d, cleaned.current.name)
    if facts is None:
        return None

    def ours(r: str) -> bool:  # the generation is of this ledger (S0087 round 1, red team F2)
        return pb.same_ledger(fs, r, root)

    if not pb.fresh(facts, observe, ours, engine_build=target.engine_build, today=target.today):
        return None
    if hint is not None and not within(hint, decode(facts.fingerprint.encode("ascii")).ledger_set):
        return None
    return pb.Current(cleaned.current, facts)


def recheck(
    fs: FileSystem,
    observe: Callable[[LedgerSet], Fingerprint],
    stamp: Fingerprint,
    target: Target,
) -> Callable[[], pb.Recheck]:
    """publish.md §4 step 7 (engine-jobs §7), just before the swap: the set the generation is stamped with observed
    again (a change: `moved`), then this workspace's `.open` journal (one: `paused`)."""

    def check() -> pb.Recheck:
        if observe(stamp.ledger_set) != stamp:
            return "moved"
        return "paused" if unfinished(fs, target) else "same"

    return check
