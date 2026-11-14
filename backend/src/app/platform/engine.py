"""The ledger-engine port (ARCHITECTURE.md §2.0, §2.3; ADR-0054; EP-07, EP-13).

The main sidecar never imports Beancount; the ledger engine, a separate process, does the ledger work. Use cases
reach it only through `EnginePort`. The real adapter is `app.platform.adapters.engine_client.EngineClient` (spawn,
handshake, supervision, the engine's process group); tests use a fake. Every call returns a tagged union: the typed
result, an `EngineRefusal` (the engine read the request and said no), or an `EngineFailure` (no answer: the engine
crashed, broke the protocol, is incompatible, stopped restarting, or was ended by us; or the request was too large
to send). A failed request is never resent by the client: whether to try again is the caller's choice, because a
write is not idempotent. Params that are not JSON data raise `ValueError` before anything is sent; the engine never
sees them, so they are no `EngineRefusal`.

**No time limits** (S0057; `design/specs/engine-jobs.md` §5.4): a call waits until the engine answers or ends. The
only way to stop waiting is ours: `end(reason)` (the user's Cancel, a retire after a job that ran user code, Quit
anyway), from any thread, never waiting; the call in flight then fails `cancelled`, which is no stop. **A job's
messages** (protocol version 2): a call's `on_event` receives the engine's progress, its `gate` (answered with
`proceed()`), `committed` and the reply's `decision`, in order, on the calling thread.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

type RefusalKey = Literal[
    "protocol.malformed",
    "protocol.version_mismatch",
    "protocol.unknown_op",
    "protocol.invalid_params",
    "protocol.handshake_required",
    "engine.internal",
    "engine.no_engine_lock",
    "engine.app_folder_missing",
]

type FailureKind = Literal[
    "crashed",  # the engine exited while the request was in flight (or between requests)
    "cancelled",  # we ended the engine (Cancel, Quit anyway) while the call waited: no stop
    "protocol_violation",  # the reply was not a valid reply to this request; the engine was stopped
    "incompatible",  # the engine speaks another protocol version; never retried
    "start_failed",  # the engine did not start or did not complete the handshake
    "unavailable",  # the engine stopped too often in the restart window; not restarted until the window passes
    "closed",  # the client was closed
    "too_large",  # the request is longer than the protocol's line limit; nothing was sent, the engine is untouched
    "app_folder_missing",  # no engine for the job's next part: the app folder is not the one of the start (S0102)
]


type EndReason = Literal["cancel", "retire", "quit_anyway"]
"""Why we end an engine (§5.2; `close` is `close()`'s own): recorded before the signal, it wins over the exit status."""

type EndCause = Literal["cancel", "retire", "quit_anyway", "close", "stop", "input_closed"]
"""Who ended an engine, as its end report says (`EngineEnd.reason`): a caller's `EndReason`, `close`, `stop` or
`input_closed`."""

type Phase = Literal[
    "reading",
    "checking_change",
    "saving",
    "updating_views",
]
"""A job's phase (engine-jobs §5.4), as the engine reports it; the protocol's `Phase` (held equal by a test)."""
PHASES: frozenset[str] = frozenset({"reading", "checking_change", "saving", "updating_views"})

type DecisionScalar = str | int | bool | None


@dataclass(frozen=True, slots=True)
class Progress:
    """The job's phase changed: shown, never judged (§5.4)."""

    phase: Phase


@dataclass(frozen=True, slots=True)
class GateReached:
    """The job reached its commit point and waits for `proceed()` (§4, §6.1 step 7)."""


@dataclass(frozen=True, slots=True)
class Committed:
    """The save landed: its closed record is durable (§6.1 step 9)."""


@dataclass(frozen=True, slots=True)
class Decision:
    """An engine decision carried by a reply, for the decision log (§12): content-free scalars only."""

    table: str
    version: int
    inputs: Mapping[str, DecisionScalar]
    output: str


type EngineEvent = Progress | GateReached | Committed | Decision


@dataclass(frozen=True, slots=True)
class EngineEnd:
    """One engine's end, reported once its process group was signalled and its leader reaped (§5.2): `serial` is
    its spawn's number in this client; `reason` says who ended it: a caller's end (`cancel`, `retire`,
    `quit_anyway`), `close`, `stop` (the supervision ended a live engine after a failure: a violation before the
    commit point, a failed or interrupted start, an interrupted wait), `input_closed` (it ended at the end of the
    input we closed after a violation past the commit point), or None: it ended by itself, and a SIGKILL then was
    not ours (§5.3: `killed_by_system`); `returncode` as `subprocess` gives it (a negative number is the signal);
    `max_rss_kib` its peak memory."""

    serial: int
    pid: int
    reason: EndCause | None
    returncode: int
    max_rss_kib: int


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """What the engine reported at the handshake."""

    protocol: int
    engine_version: str
    beancount_version: str


@dataclass(frozen=True, slots=True)
class Pong:
    """The engine is up and reading requests."""


class AppFolderChangedError(OSError):
    """The app folder is missing, or another object stands at its path, since the sidecar's start (S0102, BL-0244):
    nothing of ours is written there; the jobs that need an engine end `ENGINE_UNAVAILABLE {app_folder_missing}`."""


@dataclass(frozen=True, slots=True)
class EngineRefusal:
    """The engine read the request and refused it, with a stable key and a plain message (and the engine's decision,
    if the refusal carried one)."""

    key: RefusalKey
    message: str
    decision: Decision | None = None


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """No answer from the engine (or, for `too_large`, nothing sent). `detail` is for the log, not for the user."""

    kind: FailureKind
    detail: str


# -- Recover-all's ops (S0062; engine-jobs §8.1, §11), as the sidecar decodes them -----------------------------------

type EntryKind = Literal["file", "dir", "link", "other"]


@dataclass(frozen=True, slots=True)
class JournalFile:
    """One file a journal names: canonical path, `path_key`, and whether the save was creating it."""

    path: str
    path_key: str
    created: bool


@dataclass(frozen=True, slots=True)
class ListedJournal:
    """An `.open` record the listing found. `readable` False: it names nothing to match on (blocks every open)."""

    journal_dir: str
    name: str
    readable: bool
    workspace_key: str | None
    lineage: str | None
    files: tuple[JournalFile, ...]
    errno: int | None


@dataclass(frozen=True, slots=True)
class UnlistedFolder:
    """A journal folder that could not be synced, opened or listed (blocks every open). `linked`: a link stands in
    its place (or in place of its key folder; `folder` names the link) and holds an `.open` record or could not be
    listed through (S0077)."""

    folder: str
    errno: int | None
    linked: bool = False


@dataclass(frozen=True, slots=True)
class JournalListing:
    journals: tuple[ListedJournal, ...]
    unlisted: tuple[UnlistedFolder, ...]
    parts_removed: int


@dataclass(frozen=True, slots=True)
class EntrySeen:
    kind: EntryKind
    sha256: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class KeptCopySeen:
    name: str
    path: str
    seen: EntrySeen


@dataclass(frozen=True, slots=True)
class InspectedFile:
    """What stands at one file of a stopped journal now, and its kept copies (§11: Show both, Show it).
    `backup`: the path of a replaced file's backup when it is there and holds its old version, hashed again by the
    engine; None whenever that is not known (the text then promises no backup; S0090: a path, so a drive stop can say
    where the old version is)."""

    path: str
    created: bool
    reachable: bool
    now: EntrySeen | None
    kept: tuple[KeptCopySeen, ...]
    errno: int | None
    backup: str | None = None


type TryOutcomeKind = Literal["resolved", "stopped", "failed"]


@dataclass(frozen=True, slots=True)
class TryOutcome:
    """One journal's try (§8.1 steps 1-4)."""

    outcome: TryOutcomeKind
    cause: str | None
    path: str | None
    entry: str | None
    detail: str | None
    errno: int | None
    inspection: tuple[InspectedFile, ...] | None


type ActOutcomeKind = Literal["done", "refused", "failed"]


@dataclass(frozen=True, slots=True)
class ActOutcome:
    """A blocked-screen action's answer: `done`, `refused` (nothing written; `reason`), `failed` (`errno`)."""

    outcome: ActOutcomeKind
    reason: str | None
    path: str | None
    detail: str | None
    errno: int | None


type SaveOutcomeKind = Literal["saved", "refused", "not_saved", "unfinished"]


@dataclass(frozen=True, slots=True)
class SaveAnswer:
    """The engine's `save` result (engine-jobs §6.1, §11; S0063): `saved`; `refused` before the commit point (nothing
    written); `not_saved` (the writer's refusal or failure; `open_journal`: writes paused until recovery);
    `unfinished` (a file may have been replaced, the rollback stopped: `cause`, `cause_path`). `unchanged`: every file
    the save would write was hashed again and equals its pre bytes; `changed`: the first file it found not at them
    (S0100). `entry`, `detail`: the folder and the ACL entry of a `permission` refusal (`entry` also the folder of
    `linked_folder`, `folder_appeared` and `app_folder_missing`)."""

    outcome: SaveOutcomeKind
    reason: str | None
    path: str | None
    entry: str | None
    detail: str | None
    unchanged: bool
    open_journal: bool
    cause: str | None
    cause_path: str | None
    published: bool
    changed: str | None = None


type RebuildOutcome = Literal["loaded", "moved", "fresh", "paused", "refused"]
type Publish = Literal["published", "fresh", "inexact", "replaced", "incomplete"]
type LoadRefusal = Literal["number_failed", "beancount_failed"]
"""Beancount's own load raised on the ledger (S0098): its arithmetic failed on the transaction the line names, or
anything else of its load failed (a plugin's entry its checks cannot read). bean-check fails on the same ledger."""
type OpenRefusal = Literal["encrypted", "number_failed", "beancount_failed"]


@dataclass(frozen=True, slots=True)
class RebuildAnswer:
    """The result of the engine's `rebuild` (engine-jobs §6.7, §7; ADR-0068 D1; S0070) and its publish
    (`design/specs/mirror/publish.md` §4; S0087, BL-0113): `loaded` (Beancount's own result for the disk as observed;
    `entries` and `errors` its counts), `moved` (try again with `fingerprint`, opaque here), `fresh` (the published
    generation already describes the disk: nothing loaded), `paused` (an unfinished save's `.open` journal: not
    published) or `refused` (Beancount's own load raised on the ledger: `refusal` why, `refused` the file, `line` the
    line when known; S0098). `publish`: what became of a loaded result (`published`, or why not), `fresh`, or None.
    `number` and `ledger_version` name the current generation when published or fresh. `removed`, `remove_failed`:
    the cleanup's counts. It wrote only the workspace's mirror folder; the ledger's plugins ran in that engine."""

    outcome: RebuildOutcome
    fingerprint: str
    entries: int
    errors: int
    publish: Publish | None
    number: int | None
    ledger_version: str | None
    removed: int
    remove_failed: int
    refusal: LoadRefusal | None = None
    refused: str | None = None
    line: int | None = None


type OpenOutcome = Literal["loaded", "moved", "fresh", "paused", "refused"]


@dataclass(frozen=True, slots=True)
class OpenAnswer:
    """The result of the engine's `open` (engine-jobs §2, §7; S0088, BL-0152) and its publish
    (`design/specs/mirror/publish.md` §4): `loaded` (Beancount's result for the disk the overlay load parsed;
    `entries` and `errors` its counts), `moved` (the disk changed before the swap: run it again), `fresh` (the
    published generation already describes the disk: nothing loaded), `paused` (an unfinished save's `.open`
    journal) or `refused` (nothing loaded: `refusal` says why, `refused` names the file, `line` the line when known:
    an encrypted file, ADR-0059; or Beancount's own load raised on the ledger, S0098). `publish`, `number`,
    `ledger_version`, `removed`, `remove_failed`: as for the rebuild. It wrote only the workspace's mirror folder; the
    ledger's plugins ran in that engine."""

    outcome: OpenOutcome
    entries: int
    errors: int
    publish: Publish | None
    number: int | None
    ledger_version: str | None
    removed: int
    remove_failed: int
    refused: str | None
    refusal: OpenRefusal | None = None
    line: int | None = None


type PreviewOutcome = Literal["prepared", "refused", "moved", "paused", "stale"]
type PreviewStep = Literal["plan", "apply", "parse", "diff", "spans", "load", "errors"]
type PreviewStale = Literal["unpublished", "other_generation", "not_fresh"]


@dataclass(frozen=True, slots=True)
class PreviewFile:
    """A file a prepared plan writes: its path, the SHA-256 of its bytes now and of its new text, and the text."""

    path: str
    pre: str
    post: str
    text: str


@dataclass(frozen=True, slots=True)
class PreviewError:
    """A new error the change brings (ADR-0067: listed, never a refusal): its key's SHA-256, Beancount's type name
    and message, and where Beancount puts it."""

    key: str
    type: str
    message: str
    path: str | None
    line: int | None


@dataclass(frozen=True, slots=True)
class PreviewAnswer:
    """The result of the engine's `preview` (engine-jobs §2, §7; S0116): `prepared` (the write plan: `files`,
    `fingerprint` for the save's gate, `new_errors`, `errors` in the candidate), `refused` (`step`, `reason`, `path`,
    `detail`, `line`), `moved` (run it again in a fresh engine), `paused` (an unfinished save) or `stale` (`stale`: the
    published generation cannot be the plan's baseline). It wrote nothing; the ledger's plugins ran in that engine.
    The sidecar's preview job is `app.platform.engine_jobs.preview`; its use case and the `PlanStore` are S0117's."""

    outcome: PreviewOutcome
    files: tuple[PreviewFile, ...]
    fingerprint: str | None
    new_errors: tuple[PreviewError, ...]
    errors: int
    step: PreviewStep | None
    reason: str | None
    path: str | None
    detail: str | None
    line: int | None
    stale: PreviewStale | None


type CreateOutcomeKind = Literal["created", "not_created"]


@dataclass(frozen=True, slots=True)
class CreateAnswer:
    """The engine's `create` result (engine-jobs §8.2, §11; S0080): `created` (`.finzytrack/` holds the plan's bytes
    and is durable) or `not_created` with its `reason`; `folder` the target as requested, `path` the entry it is
    about, `detail` the ACL entry or how many load errors, `errno` the system's answer; `left` what the job left in the
    folder because it could not be sure each is only its own, by path relative to it (None: not cleaned up or
    listed)."""

    outcome: CreateOutcomeKind
    reason: str | None
    folder: str
    path: str | None
    detail: str | None
    errno: int | None
    left: tuple[str, ...] | None


type ManifestSeen = Literal["ours", "foreign", "absent", "unknown"]


@dataclass(frozen=True, slots=True)
class CreateFacts:
    """The engine's `create_outcome` result (§1 "resolved from disk"; S0080): what stands at `.finzytrack` after the
    target's sync, and the entries at the job's names that may be its own (None: not listed)."""

    manifest: ManifestSeen
    left: tuple[str, ...] | None
    errno: int | None


type RecordSeen = Literal["none", "part", "open", "closed", "resolved", "kept", "unknown"]


@dataclass(frozen=True, slots=True)
class OutcomeFacts:
    """The engine's `save_outcome` result (§6.6): the record's state after its folder's sync, and with no record, a
    `.part` or `.resolved`, whether every target equals its pre bytes (`path`: the first that does not)."""

    record: RecordSeen
    unchanged: bool | None
    path: str | None
    errno: int | None


class EnginePort(Protocol):
    def start(self) -> EngineInfo | EngineFailure:
        """Start the engine if it is not running and complete the handshake (no time limit)."""
        ...

    def ping(self) -> Pong | EngineRefusal | EngineFailure:
        """Check that the engine answers."""
        ...

    def call(
        self,
        op: str,
        params: Mapping[str, object],
        *,
        on_event: Callable[[EngineEvent], None] | None = None,
        spawn: bool = True,
        counted: bool = True,
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        """Send one request and return its `result` object as plain data. No time limit: it waits until the engine
        answers or ends, or `end` ends it. `on_event` receives the job's messages, in order, on this thread. Without
        `spawn` the request goes only to an engine that is ready, and fails otherwise: nothing is started (the
        scheduler's call inside a job, whose engine the queue chose; S0058). `counted` False: an end of the engine
        during it is no stop toward the bound (the rebuild after a save, engine-jobs §5.3; S0070)."""
        ...

    def proceed(self) -> bool:
        """Answer the pending gate of the call in flight with `proceed`; False (nothing sent) if no gate is pending."""
        ...

    def end(self, reason: EndReason) -> None:
        """End the engine for this reason, recorded before its group is signalled (§5.2). Never waits; callable from
        any thread. A retire is for an idle engine only."""
        ...

    def close(self) -> None:
        """Signal the engine's group once, then wait for the drain of the engines that are ending and for the close
        to be logged, for a bounded time (engine-jobs §9.3 step 4); later calls fail with `closed`."""
        ...
