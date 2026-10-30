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
    "waiting_for_files",
    "saving",
    "updating_views",
    "checking_last_change",
]
"""What a job is doing (engine-jobs §5.4): the UI words them "Reading your ledger…", "Checking your change…",
"Waiting for your ledger files…", "Saving…", "Updating your views…", "Checking your last change…"."""
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
    """A journal folder that could not be synced, opened or listed: it blocks every open."""

    folder: AbsPath
    errno: int | None


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
    """What stands at one file of a stopped journal now, and its kept copies (§11: Show both, Show it)."""

    path: AbsPath
    created: bool
    reachable: bool
    now: EntrySeen | None
    kept: list[KeptCopySeen]
    errno: int | None


class RecoverTryResult(Message):
    """One journal's try (§8.1 steps 1-4): `resolved`, `stopped` (blocking reason `stopped`) or `failed` (reason
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
_SEEN = {"kind": "file", "sha256": "0" * 64, "size": 21}


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
            "unlisted": [],
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
