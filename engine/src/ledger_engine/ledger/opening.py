"""The open job, inside the engine (`design/specs/engine-jobs.md` §2's `open`, §7; `design/specs/mirror/publish.md`
§4, §8; ADR-0015, ADR-0064 decision 9, ADR-0012 F-6, ADR-0059; S0088, BL-0152).

When a workspace opens (the switch and the boot, BL-0154), its mirror must describe the disk before any save is
offered: the save's gate refuses `mirror_stale` while no fresh generation is published (publish.md §7). The open is
the job that publishes it. It reads the ledger and writes only the workspace's mirror folder in the app's cache
(owner, 2026-11-08, "Only its own mirror": never the ledger, the journals, `.finzytrack/` or another key's folder).

`run(ports, root, job, target)` (it runs the ledger's plugins, so the sidecar retires its engine after it):
0. the mirror folder and its cleanup (publish.md §4 step 1), first in every try, whatever follows;
1. this workspace's `.open` journal (one, or a journal folder that cannot be listed: `paused`; §7, ADR-0012 F-6: no
   result is returned over an unfinished save, and nothing is loaded);
2. a published generation that is already fresh (§8: this ledger's, its own set unchanged on disk, this engine build
   and DDL, today) ends the job: `fresh`, nothing loaded or built;
3. the overlay load of `root` (`overlay.load`, nothing in its overlay: Beancount's own result, its errors included,
   and the fingerprint of exactly the bytes it parsed, its `include` patterns, absent paths, documents roots and
   `document` files included); an encrypted file is refused (`refused`, the file named; ADR-0059: never decrypted);
4. its publish (publish.md §4 steps 2 to 8, `mirror.publish.publish`), stamped with that fingerprint, with the
   re-check just before the swap: the stamped set observed again (a change: `moved`, nothing published; the sidecar
   runs the job again in a fresh engine, at most 3 tries in a row, then `LEDGER_CHANGING`) and the `.open` journal
   looked for again (`paused`).

The answer carries counts, the generation's number and `ledger_version`, and for a refusal the file's path (the
sidecar never logs it, INV-23). Unlike the rebuild, the open needs no hint: the overlay load finds the ledger set
itself, so its stamp names every pattern the ledger includes (the rebuild's limit "the stamp's globs" does not apply).
Any port operation may raise `OSError`, and a policy refusal other than an encrypted file cannot happen under the
desktop policy: the server answers both `engine.internal`.
"""

from __future__ import annotations

import gc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import overlay, publishing
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.store import SqliteStore
from ledger_engine.tla import tla_action

type Phase = Literal["reading"]
PHASE: Final[Phase] = "reading"
type Outcome = Literal["loaded", "moved", "fresh", "paused", "refused"]

Target = publishing.Target


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


def _overlay_load(root: str) -> overlay.LoadResult:
    """Beancount's full load of the disk (the overlay load, nothing in its overlay), the garbage collector paused while
    it runs (ADR-0008 amendment)."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        return overlay.load(root, {}, DESKTOP)
    finally:
        if collecting:
            gc.enable()


def _observe(ledger_set: LedgerSet) -> Fingerprint:
    return _fingerprint.observe(ledger_set)


@dataclass(frozen=True, slots=True)
class Ports:
    """The open's effects: the overlay load of a root, the observation of a ledger set, and the publish's: the
    file-system port, the mirror's store and the name's random tag."""

    load: Callable[[str], overlay.LoadResult] = _overlay_load
    observe: Callable[[LedgerSet], Fingerprint] = _observe
    fs: FileSystem = field(default_factory=for_this_os)
    store: pb.Store = field(default_factory=SqliteStore)
    tag: Callable[[], str] = pb.new_tag


@dataclass(frozen=True, slots=True)
class Answer:
    """`loaded` (Beancount's result for the disk the load parsed; `published` says what became of it), `moved` (the
    disk changed before the swap: nothing published), `fresh` (the published generation already describes the disk:
    nothing loaded; `published` is it), `paused` (an unfinished save's `.open` journal: nothing published) or
    `refused` (an encrypted file, `refused` its path: nothing loaded). `entries` and `errors`: the load's counts
    (`loaded` only). `cleaned`: what the cleanup did."""

    outcome: Outcome
    entries: int = 0
    errors: int = 0
    published: pb.Published | pb.NotPublished | None = None
    cleaned: pb.Cleaned | None = None
    refused: str | None = None


@tla_action("StartPublish", "P_Obs", "P_Read", model="mirror-publish")
def run(ports: Ports, root: str, job: Job, target: Target) -> Answer:
    """The module's docstring: reads the ledger; writes only the workspace's mirror folder in the app's cache."""

    def observe(fp: Fingerprint) -> Fingerprint:
        return ports.observe(fp.ledger_set)

    with pb.open_folder(ports.fs, target.app_cache, target.workspace_key) as d:  # step 0 (publish.md §4 step 1)
        cleaned = pb.clean(d, ports.store, target.now_ns)
        if publishing.unfinished(ports.fs, target):  # step 1 (engine-jobs §7)
            return Answer("paused", cleaned=cleaned)
        cur = publishing.fresh_current(d, ports.store, cleaned, observe, ports.fs, root, target)  # step 2
        if cur is not None:
            return Answer("fresh", published=pb.Published(cur.gen, cur.meta.ledger_version), cleaned=cleaned)
        job.progress(PHASE)
        loaded = ports.load(root)  # step 3: Beancount's full load (user code)
        if isinstance(loaded, overlay.LoadRefused):
            if loaded.reason == "encrypted":
                return Answer("refused", cleaned=cleaned, refused=loaded.subject)
            msg = f"the desktop policy refused a load: {loaded.reason}"
            raise RuntimeError(msg)  # cannot happen under `DESKTOP`; the server answers `engine.internal`
        stamp = loaded.fingerprint  # exactly the bytes the load parsed (the overlay's `Loaded.fingerprint`)
        out = pb.publish(  # step 4
            d,
            cleaned,
            ports.store,
            (loaded.entries, loaded.errors, loaded.options),
            stamp,
            floor=target.floor,
            today=target.today,
            built_at=target.built_at,
            engine_build=target.engine_build,
            recheck=publishing.recheck(ports.fs, ports.observe, stamp, target),
            tag=ports.tag(),
        )
    if isinstance(out, pb.NotPublished) and out.why in ("moved", "paused"):
        return Answer(out.why, cleaned=cleaned)
    return Answer("loaded", len(loaded.entries), len(loaded.errors), published=out, cleaned=cleaned)
