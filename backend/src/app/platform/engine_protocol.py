"""The main side's codec for the engine protocol (ADR-0054 decision 2; EP-06: one parser per edge).

The protocol's source is the engine's message models; `contracts/engine-protocol.schema.json` is generated from
them. The main sidecar cannot import the engine, so this module decodes replies strictly by hand, and
`tests/platform/test_engine_protocol.py` holds it to the committed schema: same version, ops, refusal keys and line
limit, every schema example decodes, and a mutated reply is refused here exactly when the schema refuses it.

Pure: no I/O. `decode_reply` reads one line the engine sent while a request is in flight: `Accepted` or an
`EngineRefusal` (the reply, with the engine's `decision` if it carries one), one of the job's messages (`Progress`,
`GateReached`, `Committed`; version 2, S0057), or a `Violation` (not a valid message for this request; the client
then stops the engine). Whether a job message comes in a valid order (one gate; `committed` only after `proceed`) is
the client's to check: it knows what it sent. `encode_proceed` is the sidecar's answer to a gate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.platform.engine import (
    PHASES,
    ActOutcome,
    ActOutcomeKind,
    Committed,
    Decision,
    DecisionScalar,
    EngineInfo,
    EngineRefusal,
    EntryKind,
    EntrySeen,
    GateReached,
    InspectedFile,
    JournalFile,
    JournalListing,
    KeptCopySeen,
    ListedJournal,
    Phase,
    Pong,
    Progress,
    RefusalKey,
    TryOutcome,
    TryOutcomeKind,
    UnlistedFolder,
)

PROTOCOL_VERSION = 2
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_ID = 2**31 - 1
OPS = ("handshake", "ping", "recover_list", "recover_try", "recover_act")
# The members of `RefusalKey` (the contract test checks both against the schema).
REFUSAL_KEYS = frozenset(
    {
        "protocol.malformed",
        "protocol.version_mismatch",
        "protocol.unknown_op",
        "protocol.invalid_params",
        "protocol.handshake_required",
        "engine.internal",
        "engine.no_engine_lock",
    }
)
OK_KEYS = frozenset({"v", "id", "op", "ok", "result"})
REFUSAL_REPLY_KEYS = frozenset({"v", "id", "op", "ok", "error"})
DECISION_KEY = "decision"  # optional on both kinds of reply
PROGRESS_KEYS = frozenset({"v", "id", "op", "event", "phase"})
EVENT_KEYS = frozenset({"v", "id", "op", "event"})  # gate, committed
DECISION_KEYS = frozenset({"table", "version", "inputs", "output"})
NAME_CHARS = 64  # the longest decision table name and output


@dataclass(frozen=True, slots=True)
class Accepted:
    """An accepted reply's `result` object, still plain data; the op's decoder types it."""

    result: Mapping[str, object]
    decision: Decision | None = None


@dataclass(frozen=True, slots=True)
class Violation:
    """Not a valid reply to the request in flight."""

    detail: str


def encode_request(rid: int, op: str, params: Mapping[str, object]) -> bytes:
    """One request line. `params` must be JSON data (no NaN or infinity)."""
    body = {"v": PROTOCOL_VERSION, "id": rid, "op": op, "params": dict(params)}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"


def encode_proceed(rid: int, op: str) -> bytes:
    """The sidecar's answer to the gate of request `rid` (engine-jobs §4)."""
    body = {"v": PROTOCOL_VERSION, "id": rid, "op": op, "control": "proceed"}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        msg = "duplicate key"
        raise ValueError(msg)
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def _is_id(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_ID


def _is_int(value: object) -> bool:
    return type(value) is int


def foreign_version(line: bytes) -> int | None:
    """The protocol version of a reply from an engine that speaks another one, else None.

    Every message carries `v`, the sender's protocol version, at its top level; that is the one part of the
    protocol no version may change (S0035 verifier F2). A reply whose `v` is an integer other than
    `PROTOCOL_VERSION` is read no further: its shape belongs to a protocol this side does not know.
    """
    try:
        data: object = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    v = cast("dict[str, object]", data).get("v")
    return v if type(v) is int and v != PROTOCOL_VERSION else None


def decode_reply(
    line: bytes, rid: int, op: str
) -> Accepted | EngineRefusal | Progress | GateReached | Committed | Violation:
    """Decode one line the engine sent while request `rid` for `op` is in flight."""
    if not line.endswith(b"\n"):
        return Violation("the reply line was cut off (no newline before the end of the stream)")
    if len(line) - 1 > MAX_LINE_BYTES:
        return Violation(f"the reply is longer than {MAX_LINE_BYTES} bytes")
    try:
        data: object = json.loads(
            line.decode("utf-8"), object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant
        )
    except ValueError:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return Violation("the reply is not one JSON value in UTF-8")
    except RecursionError:  # S0035 verifier F4(a)
        return Violation("the reply nests too deeply")
    if not isinstance(data, dict):
        return Violation("the reply is not a JSON object")
    reply = cast("dict[str, object]", data)
    if "event" in reply:
        return _event(reply, rid, op)
    ok = reply.get("ok")
    if type(ok) is not bool:
        return Violation("`ok` must be true or false")
    decision: Decision | None = None
    if DECISION_KEY in reply:
        found = _decision(reply.pop(DECISION_KEY))
        if isinstance(found, Violation):
            return found
        decision = found
    if frozenset(reply) != (OK_KEYS if ok else REFUSAL_REPLY_KEYS):
        return Violation(f"unexpected keys {sorted(reply)}")
    if not (_is_int(reply["v"]) and reply["v"] == PROTOCOL_VERSION):
        return Violation(f"`v` is not {PROTOCOL_VERSION}")
    if ok:
        if not (_is_id(reply["id"]) and reply["id"] == rid) or reply["op"] != op:
            return Violation("the reply answers another request")
        result = reply["result"]
        if not isinstance(result, dict):
            return Violation("`result` must be an object")
        return Accepted(cast("dict[str, object]", result), decision)
    refusal = _refusal(reply, rid, op)
    if isinstance(refusal, EngineRefusal) and decision is not None:
        return EngineRefusal(refusal.key, refusal.message, decision)
    return refusal


def _event(message: dict[str, object], rid: int, op: str) -> Progress | GateReached | Committed | Violation:
    """A job message (version 2): `progress` with a phase, `gate`, or `committed`, for this request."""
    event = message.get("event")
    if not isinstance(event, str) or event not in ("progress", "gate", "committed"):
        return Violation("`event` is not one of progress, gate, committed")
    if frozenset(message) != (PROGRESS_KEYS if event == "progress" else EVENT_KEYS):
        return Violation(f"unexpected keys {sorted(message)} in a {event} message")
    if not (_is_int(message["v"]) and message["v"] == PROTOCOL_VERSION):
        return Violation(f"`v` is not {PROTOCOL_VERSION}")
    if not (_is_id(message["id"]) and message["id"] == rid) or message["op"] != op:
        return Violation(f"the {event} message is for another request")
    if event == "gate":
        return GateReached()
    if event == "committed":
        return Committed()
    phase = message["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        return Violation("`phase` is not a known phase")
    return Progress(cast("Phase", phase))


def _decision(value: object) -> Decision | Violation | None:
    """The `decision` of a reply (§12), or None when it is null (the schema allows null: no decision)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return Violation("`decision` must be an object")
    body = cast("dict[str, object]", value)
    if frozenset(body) != DECISION_KEYS:
        return Violation(f"unexpected decision keys {sorted(body)}")
    table, version, inputs, output = body["table"], body["version"], body["inputs"], body["output"]
    if not (isinstance(table, str) and 1 <= len(table) <= NAME_CHARS):
        return Violation("the decision's `table` must be a short name")
    if not (_is_int(version) and cast("int", version) >= 1):
        return Violation("the decision's `version` must be a positive integer")
    if not (isinstance(output, str) and 1 <= len(output) <= NAME_CHARS):
        return Violation("the decision's `output` must be a short name")
    if not isinstance(inputs, dict):
        return Violation("the decision's `inputs` must be an object")
    flat = cast("dict[str, object]", inputs)
    if not all(v is None or isinstance(v, (str, bool)) or _is_int(v) for v in flat.values()):
        return Violation("the decision's `inputs` must be flat scalars")
    return Decision(table, cast("int", version), cast("dict[str, DecisionScalar]", flat), output)


def _refusal(reply: dict[str, object], rid: int, op: str) -> EngineRefusal | Violation:
    rid_ok = reply["id"] is None or (_is_id(reply["id"]) and reply["id"] == rid)
    op_ok = reply["op"] is None or reply["op"] == op
    if not (rid_ok and op_ok):
        return Violation("the refusal answers another request")
    error = reply["error"]
    if not isinstance(error, dict):
        return Violation("`error` must be an object")
    body = cast("dict[str, object]", error)
    key, message = body.get("key"), body.get("message")
    if set(body) != {"key", "message"} or key not in REFUSAL_KEYS or not isinstance(message, str) or not message:
        return Violation("`error` must have a known key and a message")
    return EngineRefusal(cast("RefusalKey", key), message)


def decode_info(result: Mapping[str, object]) -> EngineInfo | Violation:
    """The `handshake` result."""
    protocol, engine, beancount = result.get("protocol"), result.get("engine_version"), result.get("beancount_version")
    if set(result) != {"protocol", "engine_version", "beancount_version"}:
        return Violation(f"unexpected handshake keys {sorted(result)}")
    if not (
        _is_int(protocol) and cast("int", protocol) >= 1 and isinstance(engine, str) and isinstance(beancount, str)
    ):
        return Violation("the handshake result has the wrong types")
    return EngineInfo(protocol=cast("int", protocol), engine_version=engine, beancount_version=beancount)


def decode_pong(result: Mapping[str, object]) -> Pong | Violation:
    """The `ping` result."""
    if dict(result) != {"status": "ok"}:
        return Violation("the ping result is not {status: ok}")
    return Pong()


# -- Recover-all's results (S0062): strict, by hand, held to the schema by the contract test ----------------------

_SHA256 = re.compile(r"[0-9a-f]{64}")
_KINDS: frozenset[str] = frozenset({"file", "dir", "link", "other"})


class _Bad(Exception):
    """A result that is not of its op's shape (becomes a `Violation`)."""


def _keys(value: object, keys: frozenset[str], what: str) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(cast("dict[str, object]", value)) != keys:
        msg = f"{what} must be an object with exactly {sorted(keys)}"
        raise _Bad(msg)
    return cast("dict[str, object]", value)


def _text(value: object, what: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        msg = f"{what} must be a string"
        raise _Bad(msg)
    return value


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{what} must be a non-empty string"
        raise _Bad(msg)
    return value


def _path(value: object, what: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.startswith("/"):
        msg = f"{what} must be an absolute path"
        raise _Bad(msg)
    return value


def _int(value: object, what: str, *, nullable: bool = False, low: int | None = None) -> int | None:
    if value is None and nullable:
        return None
    if not _is_int(value) or (low is not None and cast("int", value) < low):
        msg = f"{what} must be an integer" + ("" if low is None else f" of at least {low}")
        raise _Bad(msg)
    return cast("int", value)


def _bool(value: object, what: str) -> bool:
    if type(value) is not bool:
        msg = f"{what} must be true or false"
        raise _Bad(msg)
    return value


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{what} must be a list"
        raise _Bad(msg)
    return cast("list[object]", value)


def _choice(value: object, allowed: frozenset[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        msg = f"{what} must be one of {sorted(allowed)}"
        raise _Bad(msg)
    return value


def _decoded[T](decode: Callable[[Mapping[str, object]], T], result: Mapping[str, object]) -> T | Violation:
    try:
        return decode(result)
    except _Bad as e:
        return Violation(str(e))


def _journal_file(value: object) -> JournalFile:
    o = _keys(value, frozenset({"path", "path_key", "created"}), "a journal file")
    return JournalFile(
        cast("str", _path(o["path"], "path")),
        cast("str", _path(o["path_key"], "path_key")),
        _bool(o["created"], "created"),
    )


_LISTED = frozenset({"journal_dir", "name", "readable", "workspace_key", "lineage", "files", "errno"})


def _listed(value: object) -> ListedJournal:
    o = _keys(value, _LISTED, "a listed journal")
    return ListedJournal(
        journal_dir=cast("str", _path(o["journal_dir"], "journal_dir")),
        name=_name(o["name"], "name"),
        readable=_bool(o["readable"], "readable"),
        workspace_key=_text(o["workspace_key"], "workspace_key", nullable=True),
        lineage=_text(o["lineage"], "lineage", nullable=True),
        files=tuple(_journal_file(f) for f in _list(o["files"], "files")),
        errno=_int(o["errno"], "errno", nullable=True),
    )


def _unlisted(value: object) -> UnlistedFolder:
    o = _keys(value, frozenset({"folder", "errno"}), "an unlisted folder")
    return UnlistedFolder(cast("str", _path(o["folder"], "folder")), _int(o["errno"], "errno", nullable=True))


def _listing(result: Mapping[str, object]) -> JournalListing:
    o = _keys(dict(result), frozenset({"journals", "unlisted", "parts_removed"}), "the recover_list result")
    return JournalListing(
        journals=tuple(_listed(j) for j in _list(o["journals"], "journals")),
        unlisted=tuple(_unlisted(u) for u in _list(o["unlisted"], "unlisted")),
        parts_removed=cast("int", _int(o["parts_removed"], "parts_removed", low=0)),
    )


def decode_recover_list(result: Mapping[str, object]) -> JournalListing | Violation:
    """The `recover_list` result."""
    return _decoded(_listing, result)


def _seen(value: object) -> EntrySeen:
    o = _keys(value, frozenset({"kind", "sha256", "size"}), "an entry seen")
    sha = _text(o["sha256"], "sha256", nullable=True)
    if sha is not None and not _SHA256.fullmatch(sha):
        msg = "sha256 must be 64 lowercase hex digits"
        raise _Bad(msg)
    kind = cast("EntryKind", _choice(o["kind"], _KINDS, "kind"))
    return EntrySeen(kind, sha, _int(o["size"], "size", nullable=True, low=0))


def _kept(value: object) -> KeptCopySeen:
    o = _keys(value, frozenset({"name", "path", "seen"}), "a kept copy")
    return KeptCopySeen(_name(o["name"], "name"), cast("str", _path(o["path"], "path")), _seen(o["seen"]))


_INSPECTED = frozenset({"path", "created", "reachable", "now", "kept", "errno"})


def _inspected(value: object) -> InspectedFile:
    o = _keys(value, _INSPECTED, "an inspected file")
    return InspectedFile(
        path=cast("str", _path(o["path"], "path")),
        created=_bool(o["created"], "created"),
        reachable=_bool(o["reachable"], "reachable"),
        now=None if o["now"] is None else _seen(o["now"]),
        kept=tuple(_kept(k) for k in _list(o["kept"], "kept")),
        errno=_int(o["errno"], "errno", nullable=True),
    )


_TRY = frozenset({"outcome", "cause", "path", "entry", "detail", "errno", "inspection"})
_TRY_OUTCOMES: frozenset[str] = frozenset({"resolved", "stopped", "failed"})


def _try(result: Mapping[str, object]) -> TryOutcome:
    o = _keys(dict(result), _TRY, "the recover_try result")
    inspection = o["inspection"]
    outcome = cast("TryOutcomeKind", _choice(o["outcome"], _TRY_OUTCOMES, "outcome"))
    return TryOutcome(
        outcome=outcome,
        cause=_text(o["cause"], "cause", nullable=True),
        path=_path(o["path"], "path", nullable=True),
        entry=_path(o["entry"], "entry", nullable=True),
        detail=_text(o["detail"], "detail", nullable=True),
        errno=_int(o["errno"], "errno", nullable=True),
        inspection=None if inspection is None else tuple(_inspected(i) for i in _list(inspection, "inspection")),
    )


def decode_recover_try(result: Mapping[str, object]) -> TryOutcome | Violation:
    """The `recover_try` result."""
    return _decoded(_try, result)


_ACT = frozenset({"outcome", "reason", "path", "detail", "errno"})
_ACT_OUTCOMES: frozenset[str] = frozenset({"done", "refused", "failed"})


def _act(result: Mapping[str, object]) -> ActOutcome:
    o = _keys(dict(result), _ACT, "the recover_act result")
    outcome = cast("ActOutcomeKind", _choice(o["outcome"], _ACT_OUTCOMES, "outcome"))
    return ActOutcome(
        outcome=outcome,
        reason=_text(o["reason"], "reason", nullable=True),
        path=_path(o["path"], "path", nullable=True),
        detail=_text(o["detail"], "detail", nullable=True),
        errno=_int(o["errno"], "errno", nullable=True),
    )


def decode_recover_act(result: Mapping[str, object]) -> ActOutcome | Violation:
    """The `recover_act` result."""
    return _decoded(_act, result)
