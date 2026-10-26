"""The engine protocol's message models: the single source of `contracts/engine-protocol.schema.json` (ADR-0054
decision 2; EP-14, EP-16).

Framing: JSON Lines. Each request is one JSON object on one line of the engine's stdin; each reply is one JSON
object on one line of the engine's protocol channel (its original stdout). Lines are UTF-8 and at most
`MAX_LINE_BYTES` long, the newline excluded. Every message carries `v` (the protocol version), the request `id`
and the `op`; a reply adds `ok` and then either `result` or `error {key, message}`. A refusal of a line the engine
could not read that far has `id` and `op` null. `handshake` must be the first request.

Only plain data crosses: no Beancount objects, pickles, callbacks or shared memory (ADR-0054 decision 2).
Adding an op adds its params and result models to `OPS`; changing a message's meaning bumps `PROTOCOL_VERSION`.
One thing no version may change: every message is a JSON object whose top-level `v` is its sender's protocol
version. A side that reads another `v` reads no further and treats the peer as incompatible (S0035 verifier F2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1
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
]
REFUSAL_KEYS: frozenset[str] = frozenset(get_args(RefusalKey.__value__))

RequestId = Annotated[int, Field(ge=0, le=MAX_ID)]


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


@dataclass(frozen=True, slots=True)
class OpSpec:
    """One op: its params and result models, and the example result its reply carries in the schema."""

    params: type[Message]
    result: type[Message]
    example_result: dict[str, Any]


OPS: dict[str, OpSpec] = {
    "handshake": OpSpec(
        NoParams, HandshakeResult, {"protocol": 1, "engine_version": "0.4.0", "beancount_version": "3.2.0"}
    ),
    "ping": OpSpec(NoParams, PingResult, {"status": "ok"}),
}


def _model(name: str, doc: str, fields: dict[str, Any], example: dict[str, Any]) -> type[Message]:
    """A message model built from field annotations, one per op, so the schema names each op's messages."""
    namespace: dict[str, Any] = {
        "__annotations__": fields,
        "__doc__": doc,
        "__module__": __name__,
        "__qualname__": name,
        "model_config": ConfigDict(strict=True, extra="forbid", frozen=True, json_schema_extra={"examples": [example]}),
    }
    return type(name, (Message,), namespace)


def _request_model(op: str, spec: OpSpec) -> type[Message]:
    fields = {"v": Literal[1], "id": RequestId, "op": Literal[op], "params": spec.params}
    example: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": 1, "op": op, "params": {}}
    return _model(f"{op.capitalize()}Request", f"The `{op}` request.", fields, example)


def _reply_model(op: str, spec: OpSpec) -> type[Message]:
    fields = {"v": Literal[1], "id": RequestId, "op": Literal[op], "ok": Literal[True], "result": spec.result}
    example: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": 1, "op": op, "ok": True, "result": spec.example_result}
    return _model(f"{op.capitalize()}Reply", f"The accepted reply to `{op}`.", fields, example)


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
                    "v": 1,
                    "id": 1,
                    "op": "ping",
                    "ok": False,
                    "error": {"key": "protocol.handshake_required", "message": "Send handshake first."},
                },
                {
                    "v": 1,
                    "id": None,
                    "op": None,
                    "ok": False,
                    "error": {"key": "protocol.malformed", "message": "The line is not JSON."},
                },
            ]
        },
    )
    v: Literal[1]
    id: RequestId | None
    op: str | None
    ok: Literal[False]
    error: RefusalBody


REQUEST_MODELS: dict[str, type[Message]] = {op: _request_model(op, spec) for op, spec in OPS.items()}
REPLY_MODELS: dict[str, type[Message]] = {op: _reply_model(op, spec) for op, spec in OPS.items()}
