"""The engine protocol's message models: the single source of `contracts/engine-protocol.schema.json` (ADR-0054
decision 2; EP-14, EP-16).

Framing: JSON Lines. Each request is one JSON object on one line of the engine's stdin; each reply is one JSON
object on one line of the engine's protocol channel (its original stdout). Lines are UTF-8 and at most
`MAX_LINE_BYTES` long, the newline excluded. Every message carries `v` (the protocol version), the request `id`
and the `op`; a reply adds `ok` and then either `result` or `error {key, message}`, and may carry the engine's
`decision {table, version, inputs, output}` (engine-jobs §12). A refusal of a line the engine could not read that
far has `id` and `op` null. `handshake` must be the first request.

Version 2 (S0057; engine-jobs §5.4, §5.5 bullet 5, §6.1 steps 7 and 9): before its reply, a request may send job
messages with the same `id` and `op`: `event: progress` with a `phase` (any number), `event: gate` (at most once:
the engine reached its commit point and reads nothing but `proceed` until it gets it), and `event: committed` (only
after `proceed`: the save landed). The sidecar's `control: proceed` answers a gate; it is the only line the sidecar
sends that is not a request, and it gets no reply.

Only plain data crosses: no Beancount objects, pickles, callbacks or shared memory (ADR-0054 decision 2).
Adding an op adds its params and result models to `OPS`; changing a message's meaning bumps `PROTOCOL_VERSION`.
One thing no version may change: every message is a JSON object whose top-level `v` is its sender's protocol
version. A side that reads another `v` reads no further and treats the peer as incompatible (S0035 verifier F2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 2
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_ID = 2**31 - 1  # exact in every JSON reader; the client wraps its counter below it
MAX_ECHO_CHARS = 128  # the longest `op` a refusal echoes (S0035 independent verifier F1)

type RefusalKey = Literal[
    "protocol.malformed",
    "protocol.version_mismatch",
    "protocol.unknown_op",
    "protocol.invalid_params",
    "protocol.handshake_required",
    "engine.internal",
    "engine.no_engine_lock",
]
REFUSAL_KEYS: frozenset[str] = frozenset(get_args(RefusalKey.__value__))

RequestId = Annotated[int, Field(ge=0, le=MAX_ID)]

type Phase = Literal[
    "reading",
    "checking_change",
    "saving",
    "updating_views",
]
"""What a job is doing (engine-jobs §5.4): the UI words them `msg:phase.*` (`design/generated/engine-job-texts.md`)."""
PHASES: frozenset[str] = frozenset(get_args(Phase.__value__))
DecisionScalar = str | int | bool | None


class Message(BaseModel):
    """Every message is strict (no coercion: `"1"` is not `1`), closed (no unknown keys) and immutable."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class NoParams(Message):
    """An op that takes no parameters."""


class HandshakeResult(Message):
    """What the engine is: the protocol it speaks and the versions that shape its answers."""

    protocol: int = Field(ge=1)
    engine_version: str
    beancount_version: str


class PingResult(Message):
    """The engine is up and reading requests."""

    status: Literal["ok"]


class RefusalBody(Message):
    key: RefusalKey
    message: str = Field(min_length=1)


class Decision(Message):
    """An engine decision, for the decision log (engine-jobs §12): the table and its version, its inputs and its
    output. Content-free: the inputs are flat scalars (counts, which check failed, hash equalities), never a path, a
    file name, an amount or a hash value; the engine is responsible for that, the shape keeps nesting out."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {"table": "gate", "version": 1, "inputs": {"fingerprint_equal": True, "files": 3}, "output": "pass"}
            ]
        },
    )
    table: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    inputs: dict[str, DecisionScalar]
    output: str = Field(min_length=1, max_length=64)


def _job_example(event: dict[str, Any]) -> ConfigDict:
    return ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        json_schema_extra={"examples": [{"v": 2, "id": 1, "op": "save", **event}]},
    )


class ProgressEvent(Message):
    """The job's phase changed (engine-jobs §5.4: shown, never judged)."""

    model_config = _job_example({"event": "progress", "phase": "reading"})
    v: Literal[2]
    id: RequestId
    op: str
    event: Literal["progress"]
    phase: Phase


class GateEvent(Message):
    """The job reached its commit point and waits for `proceed` (engine-jobs §4, §6.1 step 7)."""

    model_config = _job_example({"event": "gate"})
    v: Literal[2]
    id: RequestId
    op: str
    event: Literal["gate"]


class CommittedEvent(Message):
    """The save landed: its closed record is durable (engine-jobs §6.1 step 9)."""

    model_config = _job_example({"event": "committed"})
    v: Literal[2]
    id: RequestId
    op: str
    event: Literal["committed"]


class ProceedControl(Message):
    """The sidecar's answer to a gate: go past the commit point (engine-jobs §4)."""

    model_config = _job_example({"control": "proceed"})
    v: Literal[2]
    id: RequestId
    op: str
    control: Literal["proceed"]


# -- Recover-all (S0062; engine-jobs §8.1, §11; ADR-0063 decision 2) --------------------------------------------------

AbsPath = Annotated[str, Field(min_length=1, pattern=r"^/")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type EntryKind = Literal["file", "dir", "link", "other"]


class RecoverListParams(Message):
    """`app_local_data`: the app's machine-local folder; the journals are in its `local/<key>/journal/`."""

    app_local_data: AbsPath


class JournalFile(Message):
    """One file a journal names: its canonical path, its `path_key`, and whether the save was creating it."""

    path: AbsPath
    path_key: AbsPath
    created: bool


class ListedJournal(Message):
    """An `.open` record found by the listing. `readable` False: it names nothing it can be matched on (torn,
    foreign, newer, of another name, or a read error: `errno`), so it blocks every open (ADR-0063)."""

    journal_dir: AbsPath
    name: str = Field(min_length=1)
    readable: bool
    workspace_key: str | None
    lineage: str | None
    files: list[JournalFile]
    errno: int | None


class UnlistedFolder(Message):
    """A journal folder that could not be synced, opened or listed: it blocks every open. `linked`: a link stands in
    place of the key or journal folder (`folder` names the link) and it holds an `.open` record or could not be listed
    through (S0077)."""

    folder: AbsPath
    errno: int | None
    linked: bool


class RecoverListResult(Message):
    """§8.1: every `.open` journal after a `durable_sync` of each journal folder; `.part` records removed."""

    journals: list[ListedJournal]
    unlisted: list[UnlistedFolder]
    parts_removed: int = Field(ge=0)


class RecoverTryParams(Message):
    app_local_data: AbsPath
    journal_dir: AbsPath
    name: str = Field(min_length=1)


class EntrySeen(Message):
    """An entry as it stands: its kind, and for a regular file its SHA-256 and size."""

    kind: EntryKind
    sha256: Sha256 | None
    size: int | None = Field(ge=0)


class KeptCopySeen(Message):
    name: str = Field(min_length=1)
    path: AbsPath
    seen: EntrySeen


class InspectedFile(Message):
    """What stands at one file of a stopped journal now, and its kept copies (§11: Show both, Show it).
    `backup_good`: a replaced file's backup is there and holds its old version (`recover_all.Inspected`)."""

    path: AbsPath
    created: bool
    reachable: bool
    now: EntrySeen | None
    kept: list[KeptCopySeen]
    errno: int | None
    backup_good: bool


class RecoverTryResult(Message):
    """One journal's try (§8.1 steps 2-4): `resolved`, `stopped` (blocking reason `stopped`) or `failed` (reason
    `failed`), with the cause, the file (`path`), the entry that failed or blocked when it is not the file, the
    blocking ACL entry (`detail`) and the error number; for `stopped`, what stands there now (`inspection`)."""

    outcome: Literal["resolved", "stopped", "failed"]
    cause: str | None
    path: AbsPath | None
    entry: AbsPath | None
    detail: str | None
    errno: int | None
    inspection: list[InspectedFile] | None


class RecoverActParams(Message):
    """A blocked-screen action on one `.open` journal (§8.1 "Afterwards", §11): `keep`; `delete_kept` (`file`, the
    kept copy's entry name `kept`, and the `sha256` the user was shown); `delete_created` (`file`, `sha256`)."""

    app_local_data: AbsPath
    journal_dir: AbsPath
    name: str = Field(min_length=1)
    action: Literal["keep", "delete_kept", "delete_created"]
    file: AbsPath | None = None
    kept: str | None = None
    sha256: Sha256 | None = None


class RecoverActResult(Message):
    """`done`; `refused` (nothing written: `reason`, `path`, `detail`); `failed` (an error the system answered)."""

    outcome: Literal["done", "refused", "failed"]
    reason: str | None
    path: AbsPath | None
    detail: str | None
    errno: int | None


# -- The save (S0063; engine-jobs §6.1, §6.6, §11) ---------------------------------------------------------------------

SessionId = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]  # lower case only: never two that differ in case
JobName = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
Component = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[^/\x00]+$")
]  # one name; not . or .. (the handler)


class SaveChange(Message):
    """One file the save writes: its path as the ledger names it, the SHA-256 of its bytes in the plan (null: the save
    creates it), and its new text (a ledger file is UTF-8 text; whole files until the contract's edits, BL-0106)."""

    path: AbsPath
    pre: Sha256 | None
    post: str


class SaveParams(Message):
    """A confirmed plan (§2's `save`): who saves (`session`, `job`, the workspace's registry key and lineage id), the
    ledger's root, the changes, the plan's fingerprint (`ledger_set.encode`'s text) and the app's machine-local folder.
    The gate reads the published mirror in the app's cache folder the engine was started with (`--app-cache`;
    `design/specs/mirror/publish.md` §7; S0087): no request names it."""

    session: SessionId
    job: JobName
    workspace_key: Component
    lineage: str = Field(min_length=1, max_length=128)
    root: AbsPath
    changes: list[SaveChange] = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    app_local_data: AbsPath


type SaveOutcomeKind = Literal["saved", "refused", "not_saved", "unfinished"]


class SaveResult(Message):
    """How the save ended (§6.1, §11). `saved`: the closed record is durable (`published`: the mirror was published,
    never yet, BL-0113). `refused`: before the commit point, nothing written (`reason`: `unfinished_save`,
    `ledger_changed`, `mirror_stale`, `encrypted_file`). `not_saved`: the writer's `NotSaved` (`reason` its own:
    `hard_linked`, `locked`, `not_a_file`, `permission`, `changed`, `path_moved`, `read_back`, `io_error`,
    `name_not_utf8`, `linked_folder`, `no_drive_id`, `no_folder_id`; `open_journal`: writes are paused until
    recovery). `unfinished`: a file may have been replaced and the rollback stopped (`cause`, and `cause_path` its
    file: after `created_path_taken` the undo will ask about that file). `unchanged`: every file the save would write
    was hashed again and equals its pre bytes. `path`: the file it is about; `entry`: the folder a `permission`
    refusal is about, or the link a `linked_folder` refusal names; `detail`: the ACL entry that blocks it."""

    outcome: SaveOutcomeKind
    reason: str | None
    path: AbsPath | None
    entry: AbsPath | None
    detail: str | None
    unchanged: bool
    open_journal: bool
    cause: str | None
    cause_path: AbsPath | None
    published: bool


class SaveTarget(Message):
    path: AbsPath
    pre: Sha256 | None


class SaveOutcomeParams(Message):
    """§6.6: the save `session`-`job` of workspace `workspace_key`, whose engine ended without its final reply."""

    app_local_data: AbsPath
    workspace_key: Component
    session: SessionId
    job: JobName
    targets: list[SaveTarget] = Field(min_length=1)


type RecordSeen = Literal["none", "part", "open", "closed", "resolved", "kept", "unknown"]


class SaveOutcomeResult(Message):
    """The record's state after its folder's sync (`unknown`: it could not be read; `errno`), and with no record, a
    `.part` or `.resolved`, whether every target equals its pre bytes (`unchanged`; `path`: the first that does not)."""

    record: RecordSeen
    unchanged: bool | None
    path: AbsPath | None
    errno: int | None


IsoDate = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class RebuildParams(Message):
    """`rebuild` (S0070; engine-jobs §6.7, §7; ADR-0068 D1): Beancount's own load of the ledger rooted at `root`, the
    ledger set named by `fingerprint` (`ledger_set.encode`'s text: the saved plan's, or the last try's answer)
    observed before and after it, and its publish (S0087, BL-0113; `design/specs/mirror/publish.md` §4) into
    `<app cache>/workspaces/<workspace_key>/mirror/`, the app cache being the folder the engine was started with
    (`--app-cache`; no request names it): `app_local_data` and `lineage` name the `.open` journals looked
    for at the re-check, `floor` is the highest generation number the sidecar saw this session (§3), `today` the
    user's civil date (`meta.as_of`, INV-07) and `built_at` the instant."""

    root: AbsPath
    fingerprint: str = Field(min_length=1)
    app_local_data: AbsPath
    workspace_key: Component
    lineage: str = Field(min_length=1, max_length=128)
    floor: int = Field(ge=0, le=999_999_999_999)  # `mirror.publish.MAX_NUMBER`
    today: IsoDate
    built_at: str = Field(min_length=1, max_length=64)


type Publish = Literal["published", "fresh", "inexact", "replaced", "incomplete"]


class RebuildResult(Message):
    """`rebuild`'s answer: `loaded` (Beancount's result for the disk as observed; `entries` and `errors` its counts),
    `moved` (the disk changed during the load or before the swap, or the load used files outside the set:
    `fingerprint` is the set to try with next), `fresh` (the published generation already describes the disk: nothing
    loaded or built) or `paused` (an unfinished save's `.open` journal at the re-check: not published). `publish`:
    what became of a `loaded` result (`published`, or why not: `inexact`, `replaced`, `incomplete`), `fresh` for
    `fresh`, null otherwise. `number` and `ledger_version`: the current generation's when `publish` is `published` or
    `fresh`, else null. `removed`, `remove_failed`: the cleanup's counts. It runs the ledger's plugins, so the engine
    is retired after it."""

    outcome: Literal["loaded", "moved", "fresh", "paused"]
    fingerprint: str = Field(min_length=1)
    entries: int = Field(ge=0)
    errors: int = Field(ge=0)
    publish: Publish | None
    number: int | None = Field(ge=1, le=999_999_999_999)
    ledger_version: str | None = Field(pattern=r"^[1-9][0-9]*/sha256:[0-9a-f]{64}$")
    removed: int = Field(ge=0)
    remove_failed: int = Field(ge=0)


class OpenParams(Message):
    """`open` (S0088; engine-jobs §2, §7; BL-0152): the overlay load of the ledger rooted at `root` and its publish
    (`design/specs/mirror/publish.md` §4) into `<app cache>/workspaces/<workspace_key>/mirror/`, the app cache being
    the folder the engine was started with (`--app-cache`; no request names it). No fingerprint: the load finds the
    ledger set itself. `app_local_data` and `lineage` name the `.open` journals looked for; `floor`, `today` and
    `built_at` as for `rebuild`."""

    root: AbsPath
    app_local_data: AbsPath
    workspace_key: Component
    lineage: str = Field(min_length=1, max_length=128)
    floor: int = Field(ge=0, le=999_999_999_999)  # `mirror.publish.MAX_NUMBER`
    today: IsoDate
    built_at: str = Field(min_length=1, max_length=64)


class OpenResult(Message):
    """`open`'s answer: `loaded` (Beancount's result for the disk the load parsed; `entries` and `errors` its counts),
    `moved` (the disk changed before the swap: nothing published; the sidecar runs the job again), `fresh` (the
    published generation already describes the disk: nothing loaded), `paused` (an unfinished save's `.open` journal:
    nothing published) or `refused` (an encrypted file, `refused` its path; ADR-0059). `publish`: what became of a
    `loaded` result (`published`, or why not), `fresh` for `fresh`, null otherwise. `number` and `ledger_version`: the
    current generation's when `publish` is `published` or `fresh`, else null. `removed`, `remove_failed`: the
    cleanup's counts. It runs the ledger's plugins, so the engine is retired after it."""

    outcome: Literal["loaded", "moved", "fresh", "paused", "refused"]
    entries: int = Field(ge=0)
    errors: int = Field(ge=0)
    publish: Publish | None
    number: int | None = Field(ge=1, le=999_999_999_999)
    ledger_version: str | None = Field(pattern=r"^[1-9][0-9]*/sha256:[0-9a-f]{64}$")
    removed: int = Field(ge=0)
    remove_failed: int = Field(ge=0)
    refused: AbsPath | None


# -- The create (S0080; engine-jobs §1, §4, §8.2) ---------------------------------------------------------------------

RelPath = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^[^/\x00]")]  # relative; checked by the job


class CreateFile(Message):
    """One file of a new workspace: its path relative to the folder it is written in, and its text (UTF-8)."""

    path: RelPath
    text: str


class CreateParams(Message):
    """A confirmed create (§8.2): the job's name (`.finzytrack.ft-<job>`), the target folder, the workspace's files,
    which of them is the ledger's root, and the files of `.finzytrack/` (the manifest first among them). The same
    params name the create whose outcome `create_outcome` reads."""

    job: JobName
    target: AbsPath
    files: list[CreateFile] = Field(min_length=1)
    root: RelPath
    manifest: list[CreateFile] = Field(min_length=1)


class CreateResult(Message):
    """How the create ended (§8.2, §11). `created`: `.finzytrack/` holds the plan's bytes and is durable. `not_created`:
    no `.finzytrack/` of ours stands there; `reason`: `not_empty`, `not_a_folder`, `no_parent`, `permission`, `taken`,
    `read_back`, `load_failed`, `load_errors`, `load_outside`, `manifest_taken`, `path_moved`, `io_error`. `folder`:
    the target as requested; `path`: the entry it is about; `detail`: the ACL entry, or how many load errors;
    `errno`: the system's answer. `left`: what the job left in the folder after removing what it made, by path
    relative to it, because it could not be sure each is only its own (empty: nothing of its own left; null: the
    folder could not be cleaned up or listed, so the user is asked to check it)."""

    outcome: Literal["created", "not_created"]
    reason: str | None
    folder: AbsPath
    path: AbsPath | None
    detail: str | None
    errno: int | None
    left: list[RelPath] | None


class CreateOutcomeResult(Message):
    """§1 "resolved from disk": `.finzytrack` after the target's sync holds the plan's bytes (`ours`), something else
    (`foreign`), nothing (`absent`), or could not be read (`unknown`; `errno`); and `left`, the entries at the job's
    names, whatever they hold (who made a name is not known after a death, so nothing is removed; null: not listed)."""

    manifest: Literal["ours", "foreign", "absent", "unknown"]
    left: list[RelPath] | None
    errno: int | None


# -- The copy (S0081; engine-jobs §1, §4, §8.1, §8.2) -------------------------------------------------------------


class CopyParams(Message):
    """A confirmed copy (§8.2): the job's name (`.finzytrack.ft-<job>`), the target folder, the source ledger's root (as
    the user named it), the files of `.finzytrack/` (the manifest, with the copy's new lineage id), the app's local data
    folder (its `local/<key>/journal/` folders are read for `.open` journals) and the legacy write-lock files to try
    (ADR-0062). Its answers are the create's (`CreateResult`, `CreateOutcomeResult`), with the copy's own reasons:
    `legacy_saving`, `recovery_blocked`, `source_missing`, `source_unreadable`, `source_failed`, `encrypted`,
    `source_changed`, `cannot_rewrite`, `cannot_place`, `load_differs`, `plugin_modules` (and `load_failed`,
    `load_outside`). The same
    params name the copy whose outcome `copy_outcome` reads."""

    job: JobName
    target: AbsPath
    source: AbsPath
    manifest: list[CreateFile] = Field(min_length=1)
    app_local_data: AbsPath
    legacy_locks: list[AbsPath]


@dataclass(frozen=True, slots=True)
class OpSpec:
    """One op: its params and result models, the example result its reply carries in the schema, the example params
    of its request, and whether it needs the engine lock (engine-jobs §5.1: every op that reads or writes ledger or
    journal files runs only in an engine that holds it; S0062)."""

    params: type[Message]
    result: type[Message]
    example_result: dict[str, Any]
    example_params: dict[str, Any] | None = None
    needs_engine_lock: bool = False


_ALD = "/app-data/com.finzytrack.desktop"
_JD = f"{_ALD}/local/k1/journal"
_FP = '{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/ledgers/L/main.bean","v":1}'
_SEEN = {"kind": "file", "sha256": "0" * 64, "size": 21}
_CREATE = {
    "job": "j7",
    "target": "/ledgers/Books",
    "files": [{"path": "main.beancount", "text": "2026-01-01 open Assets:Cash\n"}],
    "root": "main.beancount",
    "manifest": [{"path": "workspace.json", "text": "{}\n"}],
}

_COPY = {
    "job": "j8",
    "target": "/ledgers/Books copy",
    "source": "/ledgers/Books/main.beancount",
    "manifest": [{"path": "workspace.json", "text": "{}\n"}],
    "app_local_data": _ALD,
    "legacy_locks": [],
}


OPS: dict[str, OpSpec] = {
    "handshake": OpSpec(
        NoParams, HandshakeResult, {"protocol": 2, "engine_version": "0.4.0", "beancount_version": "3.2.0"}
    ),
    "ping": OpSpec(NoParams, PingResult, {"status": "ok"}),
    "recover_list": OpSpec(
        RecoverListParams,
        RecoverListResult,
        {
            "journals": [
                {
                    "journal_dir": _JD,
                    "name": "s1-j7",
                    "readable": True,
                    "workspace_key": "k1",
                    "lineage": "lin1",
                    "files": [{"path": "/ledgers/L/main.bean", "path_key": "/ledgers/l/main.bean", "created": False}],
                    "errno": None,
                }
            ],
            "unlisted": [{"folder": f"{_ALD}/local/k2/journal", "errno": None, "linked": True}],
            "parts_removed": 0,
        },
        {"app_local_data": _ALD},
        needs_engine_lock=True,
    ),
    "recover_try": OpSpec(
        RecoverTryParams,
        RecoverTryResult,
        {
            "outcome": "stopped",
            "cause": "kept_copy",
            "path": "/ledgers/L/main.bean",
            "entry": None,
            "detail": None,
            "errno": None,
            "inspection": [
                {
                    "path": "/ledgers/L/main.bean",
                    "created": False,
                    "reachable": True,
                    "now": _SEEN,
                    "kept": [
                        {
                            "name": "main.bean.kept-s1-j7",
                            "path": "/ledgers/L/main.bean.kept-s1-j7",
                            "seen": _SEEN,
                        }
                    ],
                    "errno": None,
                    "backup_good": True,
                }
            ],
        },
        {"app_local_data": _ALD, "journal_dir": _JD, "name": "s1-j7"},
        needs_engine_lock=True,
    ),
    "recover_act": OpSpec(
        RecoverActParams,
        RecoverActResult,
        {"outcome": "done", "reason": None, "path": None, "detail": None, "errno": None},
        {"app_local_data": _ALD, "journal_dir": _JD, "name": "s1-j7", "action": "keep"},
        needs_engine_lock=True,
    ),
    "save": OpSpec(
        SaveParams,
        SaveResult,
        {
            "outcome": "saved",
            "reason": None,
            "path": None,
            "entry": None,
            "detail": None,
            "unchanged": False,
            "open_journal": False,
            "cause": None,
            "cause_path": None,
            "published": False,
        },
        {
            "session": "s1",
            "job": "j7",
            "workspace_key": "k1",
            "lineage": "lin1",
            "root": "/ledgers/L/main.bean",
            "changes": [{"path": "/ledgers/L/main.bean", "pre": "0" * 64, "post": "2026-01-01 open Assets:Cash\n"}],
            "fingerprint": _FP,
            "app_local_data": _ALD,
        },
        needs_engine_lock=True,
    ),
    "save_outcome": OpSpec(
        SaveOutcomeParams,
        SaveOutcomeResult,
        {"record": "closed", "unchanged": None, "path": None, "errno": None},
        {
            "app_local_data": _ALD,
            "workspace_key": "k1",
            "session": "s1",
            "job": "j7",
            "targets": [{"path": "/ledgers/L/main.bean", "pre": "0" * 64}],
        },
        needs_engine_lock=True,
    ),
    "rebuild": OpSpec(
        RebuildParams,
        RebuildResult,
        {
            "outcome": "loaded",
            "fingerprint": _FP,
            "entries": 3,
            "errors": 0,
            "publish": "published",
            "number": 2,
            "ledger_version": "2/sha256:" + "0" * 64,
            "removed": 1,
            "remove_failed": 0,
        },
        {
            "root": "/ledgers/L/main.bean",
            "fingerprint": _FP,
            "app_local_data": _ALD,
            "workspace_key": "k1",
            "lineage": "lin1",
            "floor": 0,
            "today": "2026-11-08",
            "built_at": "2026-11-08T10:00:00+05:30",
        },
        needs_engine_lock=True,
    ),
    "open": OpSpec(
        OpenParams,
        OpenResult,
        {
            "outcome": "loaded",
            "entries": 3,
            "errors": 0,
            "publish": "published",
            "number": 2,
            "ledger_version": "2/sha256:" + "0" * 64,
            "removed": 1,
            "remove_failed": 0,
            "refused": None,
        },
        {
            "root": "/ledgers/L/main.bean",
            "app_local_data": _ALD,
            "workspace_key": "k1",
            "lineage": "lin1",
            "floor": 0,
            "today": "2026-11-08",
            "built_at": "2026-11-08T10:00:00+05:30",
        },
        needs_engine_lock=True,
    ),
    "create": OpSpec(
        CreateParams,
        CreateResult,
        {
            "outcome": "not_created",
            "reason": "taken",
            "folder": "/ledgers/Books",
            "path": "/ledgers/Books/main.beancount",
            "detail": None,
            "errno": None,
            "left": ["prices"],
        },
        _CREATE,
        needs_engine_lock=True,
    ),
    "create_outcome": OpSpec(
        CreateParams,
        CreateOutcomeResult,
        {"manifest": "absent", "left": ["main.beancount"], "errno": None},
        _CREATE,
        needs_engine_lock=True,
    ),
    "copy": OpSpec(
        CopyParams,
        CreateResult,
        {
            "outcome": "not_created",
            "reason": "source_changed",
            "folder": "/ledgers/Books copy",
            "path": "/ledgers/Books/main.beancount",
            "detail": None,
            "errno": None,
            "left": [],
        },
        _COPY,
        needs_engine_lock=True,
    ),
    "copy_outcome": OpSpec(
        CopyParams,
        CreateOutcomeResult,
        {"manifest": "absent", "left": ["main.beancount"], "errno": None},
        _COPY,
        needs_engine_lock=True,
    ),
}


def _model(name: str, doc: str, fields: dict[str, Any], example: dict[str, Any]) -> type[Message]:
    """A message model built from field annotations, one per op, so the schema names each op's messages. A
    `decision` field is optional (absent when the reply carries none)."""
    namespace: dict[str, Any] = {
        **({"decision": None} if "decision" in fields else {}),
        "__annotations__": fields,
        "__doc__": doc,
        "__module__": __name__,
        "__qualname__": name,
        "model_config": ConfigDict(strict=True, extra="forbid", frozen=True, json_schema_extra={"examples": [example]}),
    }
    return type(name, (Message,), namespace)


def model_stem(op: str) -> str:
    """The name the schema gives an op's messages: `recover_list` -> `RecoverList` (`RecoverListRequest`, ...)."""
    return "".join(part.capitalize() for part in op.split("_"))


def _request_model(op: str, spec: OpSpec) -> type[Message]:
    fields = {"v": Literal[2], "id": RequestId, "op": Literal[op], "params": spec.params}
    params = spec.example_params or {}
    example: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": 1, "op": op, "params": params}
    return _model(f"{model_stem(op)}Request", f"The `{op}` request.", fields, example)


def _reply_model(op: str, spec: OpSpec) -> type[Message]:
    fields = {
        "v": Literal[2],
        "id": RequestId,
        "op": Literal[op],
        "ok": Literal[True],
        "result": spec.result,
        "decision": Decision | None,
    }
    example: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": 1, "op": op, "ok": True, "result": spec.example_result}
    return _model(f"{model_stem(op)}Reply", f"The accepted reply to `{op}`.", fields, example)


class Refusal(Message):
    """A refused request, or a line that was not a request. `id` and `op` are those of the request when it could
    be read that far, else null; `op` is null too when it cannot be echoed exactly (not valid Unicode, or longer
    than 128 characters)."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "v": 2,
                    "id": 1,
                    "op": "ping",
                    "ok": False,
                    "error": {"key": "protocol.handshake_required", "message": "Send handshake first."},
                },
                {
                    "v": 2,
                    "id": None,
                    "op": None,
                    "ok": False,
                    "error": {"key": "protocol.malformed", "message": "The line is not JSON."},
                },
            ]
        },
    )
    v: Literal[2]
    id: RequestId | None
    op: str | None
    ok: Literal[False]
    error: RefusalBody
    decision: Decision | None = None


REQUEST_MODELS: dict[str, type[Message]] = {op: _request_model(op, spec) for op, spec in OPS.items()}
REPLY_MODELS: dict[str, type[Message]] = {op: _reply_model(op, spec) for op, spec in OPS.items()}
JOB_MESSAGES: tuple[type[Message], ...] = (ProgressEvent, GateEvent, CommittedEvent, ProceedControl)
