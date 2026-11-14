"""The preview job, inside the engine (`design/specs/engine-jobs.md` §2's `preview`, §7; ARCHITECTURE.md §9.2 steps 2
and 3; ADR-0008, ADR-0067, ADR-0068 D1; S0116). It writes nothing.

A preview plans a change on the published mirror generation the plan was made on and checks it with the write-safety
contract (`ledger.contract.prepare`), so the save that may follow writes exactly the texts it returns, on the
fingerprint it returns. `run(ports, root, generation, mutator, request, where, job)`:
0. this workspace's `.open` journal (one, or a journal folder that cannot be listed): `paused`, nothing read (§7);
1. **the baseline**: the published generation (`mirror.publish.read_baseline`: its `meta` and its `ledger_errors`).
   It must be the generation the plan was made on (`generation`, its number), complete, built by this engine build
   and DDL, this ledger's (its root's `path_key` is `root`'s), and fresh on disk (its own ledger set observed now
   has its digest). Otherwise `stale`, nothing loaded: the generation's errors are not those of the files the plan
   edits, so the preview's list of new errors would not be true. The sidecar plans again after a rebuild. When no
   fresh generation describes the disk (`unpublished`, `not_fresh`) the answer carries a **hint** for that rebuild
   (S0118, BL-0255; the sidecar queues it): the generation's stamp when it decodes and names this `root` (the `rebuild`
   op refuses a hint of another root), else the root's own set observed now (no generation, a stamp that cannot be
   decoded, another ledger's), which the rebuild widens by its `moved` try (engine-jobs §7). `other_generation`
   carries none: the published generation may be fresh, and the sidecar plans again on it;
2. **the snapshot**: the bytes of every ledger file the generation's fingerprint names, read now, each with the
   SHA-256 the fingerprint holds (else `moved`: the disk changed since step 1); in the fingerprint's order, the root
   first (the order `route_insert` breaks ties of Fava's `insert-entry` options in);
3. **the plan**: the producer registered for `mutator` (`PRODUCERS`; ADR-0009: every mutator is an edit producer)
   turns `request` into an edit plan and its intended change; an unknown mutator, a producer's refusal or a fault
   in it refuses at step `plan`. No producer is registered yet: each mutator's slice adds its own (the contract's
   tests register a test-only one);
4. **prepare** (`contract.prepare`, with the desktop `LoadPolicy`): `Prepared` or `Refused` naming the step;
5. **the re-check** (§7), before answering: the generation still fresh on disk, the plan's fingerprint observed again
   (a change: `moved`; the sidecar runs the job again in a fresh engine), and the `.open` journal again (`paused`).

The load runs the ledger's plugins (one load with plugins per engine process): the sidecar retires the engine after
the job. The answer holds no entry (INV-10): texts, hashes, the fingerprint, the new errors.
"""

from __future__ import annotations

import gc
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import FileSystem
from ledger_engine.fs.this_os import for_this_os
from ledger_engine.ledger import contract, overlay, publishing
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger.core import entry_keys
from ledger_engine.ledger.core import semantic_diff as sd
from ledger_engine.ledger.core import source_edit as se
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, LedgerSet, UndecodableError, decode
from ledger_engine.ledger.policy import DESKTOP, LoadPolicy
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.core import schema
from ledger_engine.mirror.store import SqliteStore

type Phase = Literal["checking_change"]
PHASE: Final[Phase] = "checking_change"
type Outcome = Literal["prepared", "refused", "moved", "paused", "stale"]
type Stale = Literal["unpublished", "other_generation", "not_fresh"]

type Producer = Callable[[se.Snapshot, Mapping[str, object]], sd.Produced | se.Refusal]
"""An edit producer (ADR-0009): `(snapshot, request) -> EditPlan + IntendedChange`, or the primitives' refusal."""

PRODUCERS: Final[dict[str, Producer]] = {}
"""The producer of each mutator, by its intent-table id (`design/specs/write-path/intent-table.toml`). Empty until
the first mutator's slice (WS-WRITE stage 3) registers one."""


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


@dataclass(frozen=True, slots=True)
class Where:
    """The workspace: the app's cache folder (its mirror), its registry key and lineage id, the app's machine-local
    folder (its `.open` journals), and this engine's build (the generation must be this build's)."""

    app_cache: str
    workspace_key: str
    app_local_data: str
    lineage: str
    engine_build: str


def _overlay_load(root: str, candidates: Mapping[str, bytes], policy: LoadPolicy) -> overlay.LoadResult:
    """The overlay load with the garbage collector paused while it runs (ADR-0008 amendment)."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        return overlay.load(root, candidates, policy)
    finally:
        if collecting:
            gc.enable()


@dataclass(frozen=True, slots=True)
class Ports:
    load: contract.Load = _overlay_load
    observe: Callable[[LedgerSet], Fingerprint] = _fingerprint.observe
    read: Callable[[str], bytes] = _fingerprint.read_regular
    fs: FileSystem = field(default_factory=for_this_os)
    store: pb.BaselineStore = field(default_factory=SqliteStore)


@dataclass(frozen=True, slots=True)
class Answer:
    """`prepared` (`prepared` holds the write plan), `refused` (`refused` names the step and why), `moved` (the disk
    changed during the job: run it again), `paused` (an unfinished save's `.open` journal) or `stale` (`stale` says
    why the published generation is not the plan's baseline; `hint`, for `unpublished` and `not_fresh` only, the
    fingerprint the rebuild starts from: the module's docstring)."""

    outcome: Outcome
    prepared: contract.Prepared | None = None
    refused: contract.Refused | None = None
    stale: Stale | None = None
    hint: Fingerprint | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_fresh(ports: Ports, base: pb.Baseline, root: str, where: Where) -> bool:
    meta = base.current.meta

    def ours(r: str) -> bool:
        return pb.same_ledger(ports.fs, r, root)

    def observe(f: Fingerprint) -> Fingerprint:
        return ports.observe(f.ledger_set)

    return (
        meta.build_complete
        and meta.export_version == schema.EXPORT_VERSION
        and meta.engine_build == where.engine_build
        and pb.fresh_on_disk(meta, observe, ours)
    )


def _own_set(ports: Ports, root: str) -> Fingerprint:
    """The root alone, observed now: the hint when no stamp of this root can be read (the module's docstring)."""
    return ports.observe(LedgerSet(root, (root,), (), (), ()))


def _not_fresh(ports: Ports, root: str, stamp: Fingerprint | None) -> Answer:
    hint = stamp if stamp is not None and stamp.root == root else _own_set(ports, root)
    return Answer("stale", stale="not_fresh", hint=hint)


def _snapshot(ports: Ports, stamp: Fingerprint) -> se.Snapshot | None:
    """Every ledger file the stamp read, at the bytes it read (None: one differs now)."""
    names = [p for p, state in stamp.files if isinstance(state, Content)]
    names.sort(key=lambda p: p != stamp.root)  # the root first, the rest in the stamp's order
    files: dict[str, bytes] = {}
    for path, state in stamp.files:
        if isinstance(state, Content):
            data = ports.read(path)
            if _sha256(data) != state.sha256:
                return None
            files[path] = data
    return se.Snapshot({p: files[p] for p in names})


def _plan(producer: Producer, snapshot: se.Snapshot, request: Mapping[str, object]) -> sd.Produced | contract.Refused:
    try:
        made = producer(snapshot, request)
    except Exception as e:  # noqa: BLE001 (INV-22: a producer that fails refuses the plan; nothing was written)
        return contract.Refused("plan", "fault", None, type(e).__name__)
    if isinstance(made, se.Refusal):
        return contract.Refused("plan", str(made.reason), made.path, made.detail)
    return made


def run(
    ports: Ports,
    root: str,
    generation: int,
    mutator: str,
    request: Mapping[str, object],
    where: Where,
    job: Job,
    producers: Mapping[str, Producer] = PRODUCERS,
) -> Answer:
    """The module's docstring: steps 0 to 5. It writes nothing."""
    if publishing.unfinished(ports.fs, where):  # step 0
        return Answer("paused")
    base = pb.read_baseline(ports.fs, where.app_cache, where.workspace_key, ports.store)  # step 1
    if base is None:
        return Answer("stale", stale="unpublished", hint=_own_set(ports, root))
    if base.current.gen.number != generation:
        return Answer("stale", stale="other_generation")
    try:
        stamp = decode(base.current.meta.fingerprint.encode("ascii"))
    except (UnicodeEncodeError, UndecodableError):
        return _not_fresh(ports, root, None)
    if not _baseline_fresh(ports, base, root, where):
        return _not_fresh(ports, root, stamp)
    snapshot = _snapshot(ports, stamp)  # step 2
    if snapshot is None:
        return Answer("moved")
    producer = producers.get(mutator)  # step 3
    if producer is None:
        return Answer("refused", refused=contract.Refused("plan", "unknown_mutator", None, "no producer by that name"))
    produced = _plan(producer, snapshot, request)
    if isinstance(produced, contract.Refused):
        return Answer("refused", refused=produced)
    job.progress(PHASE)
    baseline = entry_keys.error_keys(base.errors)
    result = contract.prepare(snapshot, produced, stamp.root, baseline, DESKTOP, load=ports.load)  # step 4
    if not _baseline_fresh(ports, base, root, where):  # step 5
        return Answer("moved")
    if isinstance(result, contract.Prepared) and ports.observe(result.fingerprint.ledger_set) != result.fingerprint:
        return Answer("moved")
    if publishing.unfinished(ports.fs, where):
        return Answer("paused")
    if isinstance(result, contract.Refused):
        return Answer("refused", refused=result)
    return Answer("prepared", prepared=result)
