"""The ledger-engine port (ARCHITECTURE.md §2.0, §2.3; ADR-0054; EP-07, EP-13).

The main sidecar never imports Beancount; the ledger engine, a separate process, does the ledger work. Use cases
reach it only through `EnginePort`. The real adapter is `app.platform.adapters.engine_client.EngineClient` (spawn,
handshake, timeouts, supervision); tests use a fake. Every call returns a tagged union: the typed result, an
`EngineRefusal` (the engine read the request and said no), or an `EngineFailure` (no answer: the engine crashed,
timed out, broke the protocol, is incompatible, or stopped restarting; or the request was too large to send). A
failed request is never resent by the client: whether to try again is the caller's choice, because a write is not
idempotent. Caller errors raise `ValueError` before anything is sent: a timeout that is not a positive, finite
number, and params that are not JSON data. The engine never sees those, so they are no `EngineRefusal`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

type RefusalKey = Literal[
    "protocol.malformed",
    "protocol.version_mismatch",
    "protocol.unknown_op",
    "protocol.invalid_params",
    "protocol.handshake_required",
    "engine.internal",
]

type FailureKind = Literal[
    "crashed",  # the engine exited while the request was in flight (or between requests)
    "timeout",  # no reply within the caller's timeout; the engine was stopped
    "protocol_violation",  # the reply was not a valid reply to this request; the engine was stopped
    "incompatible",  # the engine speaks another protocol version; never retried
    "start_failed",  # the engine did not start or did not complete the handshake
    "unavailable",  # the engine stopped too often in the restart window; not restarted until the window passes
    "closed",  # the client was closed
    "too_large",  # the request is longer than the protocol's line limit; nothing was sent, the engine is untouched
]


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """What the engine reported at the handshake."""

    protocol: int
    engine_version: str
    beancount_version: str


@dataclass(frozen=True, slots=True)
class Pong:
    """The engine is up and reading requests."""


@dataclass(frozen=True, slots=True)
class EngineRefusal:
    """The engine read the request and refused it, with a stable key and a plain message."""

    key: RefusalKey
    message: str


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """No answer from the engine (or, for `too_large`, nothing sent). `detail` is for the log, not for the user."""

    kind: FailureKind
    detail: str


class EnginePort(Protocol):
    def start(self) -> EngineInfo | EngineFailure:
        """Start the engine if it is not running and complete the handshake."""
        ...

    def ping(self, timeout: float) -> Pong | EngineRefusal | EngineFailure:
        """Check that the engine answers."""
        ...

    def call(
        self, op: str, params: Mapping[str, object], timeout: float
    ) -> Mapping[str, object] | EngineRefusal | EngineFailure:
        """Send one request and return its `result` object as plain data. The timeout bounds the wait for the
        reply; a (re)start the call needs first is bounded separately by the adapter's policy."""
        ...

    def close(self) -> None:
        """Stop the engine; later calls fail with `closed`."""
        ...
