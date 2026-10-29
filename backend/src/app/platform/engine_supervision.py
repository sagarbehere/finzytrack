"""The ledger engine's supervision as a statechart: a pure transition table (S0040, amended by S0057; EP-17, EP-01;
ARCHITECTURE.md §7; `design/specs/engine-jobs.md` §5.1-§5.5).

`transition(policy, state, event) -> Step(state, effects)` decides everything about the engine's lifecycle: when to
start it, how long to wait before a restart, when it is `Unavailable`, that another protocol is `Incompatible`, that
`Closed` is final, which ends are stops, and what each start and stop is called in the log. `EngineClient`
(`app.platform.adapters.engine_client`) is the shell: it observes the process (spawned, the handshake's answer, a
reply, end of stream, an exit, our own end), feeds each observation in as an event and carries out the effects
(spawn, handshake, pause, end, close the input, discard the bytecode directory, log, fail the caller). The generated
reference of this table is `design/generated/statecharts/engine-supervision.md`.

- **States.** At rest: `Idle` (no engine: none started, after a reset, or after our own end, its stops kept),
  `Ready` (running, waiting for a request), `Crashed` (stopped, below the bound), `Unavailable` (the bound is
  reached), `Incompatible`, `Closed`. Passing, inside one call: `Starting` (the process is being made),
  `Handshaking`, `Busy` (one request in flight), `Finishing` (a reply broke the protocol after the commit point: the
  input is closed and the output read until the exit), `Restarting` (waiting out the backoff). The client's lock
  serializes `start`, `ping`, `call` and `reset`, so `Request` and `Reset` arrive only at rest. `close()` does not
  wait for that lock (engine-jobs §5.5): its `Close` arrives at rest, or in a passing state through the call that
  observes the end `close()` signalled.
- **No time limits** (§5.4): nothing in the policy times a start, a handshake, a request or an end.
- **Stops.** A failed start, a request without a valid answer (end of stream we did not cause, an invalid reply), an
  exit noticed between requests and an interrupted start or wait are stops: each is recorded with its time. Only
  the stops of the last `window` seconds count; a stop exactly one window old has left it. The stop that makes
  `max_crashes` enters `Unavailable` at once; a request then refuses until the window has let a stop go, or `Reset`.
  An incompatible engine, an answer and a refusal are no stop, and **neither is our own end** (§5.3): `Cancelled`
  (the user's Cancel or Quit anyway, in a start, a handshake, a backoff, a request, or of an idle engine; the caller
  of an ended call fails `cancelled`), `Retired` (an idle engine after a job that ran user code), `Close`.
- **After the commit point** (§5.3 table): a reply that breaks the protocol once `proceed` was sent is
  `ViolatedPastCommit`: the engine is not signalled (it may be in the middle of a replace); its input is closed
  (`CloseInput`), its output read and discarded until it ends, and then the stop is counted.
- **The bytecode directory** (§5.1): a `Spawn` may use it; a handshake that fails while it was used deletes it
  (`DiscardBytecode`) and spawns once without it, not counted as a stop.
- **Backoff.** A restart after n counted stops waits `delays[n-1]` from the last stop (the last delay repeats); time
  already passed counts. There is no "window passed" event: nothing observes time between calls, so every
  `Request` carries the time and the table prunes the stops itself.
- **Never resent.** The table never sends a request: only the caller's `Send` from `Ready` enters `Busy`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.platform.engine import EngineFailure, EngineInfo
from app.platform.engine_protocol import PROTOCOL_VERSION
from app.platform.observability.events import (
    ENGINE_BYTECODE,
    ENGINE_CLOSED,
    ENGINE_ENDED,
    ENGINE_READY,
    ENGINE_START_FAILED,
    ENGINE_STOPPED,
    ENGINE_UNAVAILABLE,
    Event,
)

type Stops = tuple[float, ...]
"""The monotonic times of the counted stops, oldest first; at most `max_crashes`."""
type StopKind = Literal["crashed", "protocol_violation", "start_failed"]
type CancelReason = Literal["cancel", "quit_anyway"]


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    """Backoff and bound for restarts. `delays[n-1]` is the wait before a start that follows n recent stops (the
    last delay repeats); `max_crashes` stops within `window` seconds make the engine `unavailable`. No time limit on
    anything else (engine-jobs §5.4)."""

    max_crashes: int = 3
    window: float = 300.0
    delays: tuple[float, ...] = (0.5, 2.0, 8.0)

    def __post_init__(self) -> None:
        """The backoff and its window are waits; each must be finite (S0035 independent verifier F3)."""
        bad = [] if self.window > 0 and math.isfinite(self.window) else [f"window={self.window!r}"]
        if not all(d >= 0 and math.isfinite(d) for d in self.delays):
            bad.append(f"delays={self.delays!r}")
        if self.max_crashes < 1:
            bad.append(f"max_crashes={self.max_crashes!r}")
        if bad:
            msg = f"RestartPolicy needs finite waits and at least one crash: {', '.join(bad)}"
            raise ValueError(msg)


# -- States ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Idle:
    """No engine: none was started yet, a reset forgot the stops, or we ended the last one (its stops kept). The
    next request starts one, after the backoff still due."""

    stops: Stops = ()


@dataclass(frozen=True, slots=True)
class Starting:
    """The engine process is being created."""

    stops: Stops


@dataclass(frozen=True, slots=True)
class Handshaking:
    """The process runs and the handshake is out; `bytecode`: the engine was started with the bytecode directory."""

    stops: Stops
    pid: int
    bytecode: bool = False


@dataclass(frozen=True, slots=True)
class Ready:
    """The engine runs, answered the handshake in our protocol, and waits for a request."""

    stops: Stops
    pid: int
    info: EngineInfo


@dataclass(frozen=True, slots=True)
class Busy:
    """One request is in flight."""

    stops: Stops
    pid: int
    info: EngineInfo


@dataclass(frozen=True, slots=True)
class Finishing:
    """A reply broke the protocol after the commit point (`detail`): the engine's input is closed and its output
    read and discarded until it ends; the stop is counted then (§5.3)."""

    stops: Stops
    pid: int
    info: EngineInfo
    detail: str


@dataclass(frozen=True, slots=True)
class Crashed:
    """The engine stopped (`last` says how) and the bound is not reached: the next request restarts it."""

    stops: Stops
    last: StopKind


@dataclass(frozen=True, slots=True)
class Restarting:
    """Waiting out the backoff before a restart (`last`: the last engine's stop, None when we ended it)."""

    stops: Stops
    last: StopKind | None


@dataclass(frozen=True, slots=True)
class Unavailable:
    """`max_crashes` stops in the window: requests are refused until the window lets one go, or a reset."""

    stops: Stops
    last: StopKind


@dataclass(frozen=True, slots=True)
class Incompatible:
    """The engine speaks another protocol: refused, and never started again until a reset."""

    failure: EngineFailure


@dataclass(frozen=True, slots=True)
class Closed:
    """The client was closed; final."""


type SupervisionState = (
    Idle
    | Starting
    | Handshaking
    | Ready
    | Busy
    | Finishing
    | Crashed
    | Restarting
    | Unavailable
    | Incompatible
    | Closed
)

# -- Events -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    """A caller needs a running engine (`start`, `ping`, `call`); `now` is the monotonic time."""

    now: float


@dataclass(frozen=True, slots=True)
class Spawned:
    """The engine process exists, with its pipes; `bytecode`: it was started with the bytecode directory."""

    pid: int
    bytecode: bool = False


@dataclass(frozen=True, slots=True)
class SpawnFailed:
    """The process could not be created (an `OSError`, or no pipes)."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class HandshakeOk:
    """A valid handshake reply; `info.protocol` may still be another protocol."""

    info: EngineInfo


@dataclass(frozen=True, slots=True)
class HandshakeForeign:
    """The handshake reply is another protocol's (another `v`, or a `protocol.version_mismatch` refusal)."""

    detail: str


@dataclass(frozen=True, slots=True)
class HandshakeFailed:
    """No valid handshake in our protocol: end of stream we did not cause, an invalid reply, or another refusal."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class BackoffElapsed:
    """The backoff pause is over."""


@dataclass(frozen=True, slots=True)
class Send:
    """The caller's request is written to the engine."""


@dataclass(frozen=True, slots=True)
class Answered:
    """A valid reply to the request in flight: a result or a refusal. The engine is fine."""


@dataclass(frozen=True, slots=True)
class Ended:
    """The engine's output ended (it exited) or its input broke while the request was in flight, and we did not end
    it; in `Finishing`, the end awaited after a violation past the commit point."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class Violated:
    """The reply is not a valid message for this request, before its commit point."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class ViolatedPastCommit:
    """The reply broke the protocol after `proceed` was sent: the engine may be in the middle of a replace."""

    detail: str


@dataclass(frozen=True, slots=True)
class Exited:
    """The engine was found exited between requests (noticed at the next request)."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class Interrupted:
    """An exception in the client itself during a start, a backoff or a wait (it goes on to the caller)."""

    detail: str
    now: float


@dataclass(frozen=True, slots=True)
class Cancelled:
    """We ended the engine with this reason (the user's Cancel, or Quit anyway), recorded before the signal: no stop
    (§5.3). Observed by the call it ended, or of an idle engine."""

    reason: CancelReason
    now: float


@dataclass(frozen=True, slots=True)
class Retired:
    """We ended an idle engine after a job that ran user code (§5.1): no stop."""


@dataclass(frozen=True, slots=True)
class Reset:
    """The user's "try again": forget the stops and an incompatible verdict."""


@dataclass(frozen=True, slots=True)
class Close:
    """Stop the engine for good (`close()`): at rest, or observed by the call in flight that it ended."""


type SupervisionEvent = (
    Request
    | Spawned
    | SpawnFailed
    | HandshakeOk
    | HandshakeForeign
    | HandshakeFailed
    | BackoffElapsed
    | Send
    | Answered
    | Ended
    | Violated
    | ViolatedPastCommit
    | Exited
    | Interrupted
    | Cancelled
    | Retired
    | Reset
    | Close
)

# -- Effects ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spawn:
    """Create the engine process, once the previous engine's group is drained (§5.1); answer with `Spawned` or
    `SpawnFailed`. `bytecode`: the bytecode directory may be used, if it is complete."""

    bytecode: bool = True


@dataclass(frozen=True, slots=True)
class Handshake:
    """Send the handshake; answer with `HandshakeOk`, `HandshakeForeign` or `HandshakeFailed`."""


@dataclass(frozen=True, slots=True)
class Pause:
    """Wait `seconds`; answer with `BackoffElapsed`."""

    seconds: float


@dataclass(frozen=True, slots=True)
class End:
    """End the engine: signal its process group once (unless it was signalled already or is reaped) and let it go;
    its reaper drains the group and reaps it (§5.2). Never waits."""


@dataclass(frozen=True, slots=True)
class CloseInput:
    """Close the engine's input and read its output to the end, discarding it; answer with `Ended` (§5.3)."""


@dataclass(frozen=True, slots=True)
class DiscardBytecode:
    """Delete the bytecode directory (§5.1); the next fill rebuilds it."""


@dataclass(frozen=True, slots=True)
class Log:
    """Write a registered event with these fields."""

    event: Event
    fields: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True, slots=True)
class Fail:
    """The caller's call ends with this failure."""

    failure: EngineFailure


type Effect = Spawn | Handshake | Pause | End | CloseInput | DiscardBytecode | Log | Fail


@dataclass(frozen=True, slots=True)
class Step:
    state: SupervisionState
    effects: tuple[Effect, ...] = ()


class IllegalTransition(Exception):
    """An event the table does not define for this state: a bug in the shell, never a runtime outcome."""

    def __init__(self, state: SupervisionState, event: SupervisionEvent) -> None:
        super().__init__(f"{type(event).__name__} is not defined in {type(state).__name__}")
        self.state = state
        self.event = event


# -- Decisions --------------------------------------------------------------------------------------------------------


def counted(policy: RestartPolicy, stops: Stops, now: float) -> Stops:
    """The stops still in the window at `now` (half-open: a stop exactly one window old is out)."""
    return tuple(t for t in stops if now - t < policy.window)[-policy.max_crashes :]


def backoff(policy: RestartPolicy, stops: Stops, now: float) -> float:
    """How long a start at `now` must still wait after these counted stops."""
    if not stops or not policy.delays:
        return 0.0
    delay = policy.delays[min(len(stops), len(policy.delays)) - 1]
    return max(0.0, stops[-1] + delay - now)


def _start(policy: RestartPolicy, stops: Stops, last: StopKind | None, now: float) -> Step:
    """A start below the bound: at once, or after the backoff still due."""
    wait = backoff(policy, stops, now)
    if wait > 0:
        return Step(Restarting(stops, last), (Pause(wait),))
    return Step(Starting(stops), (Spawn(),))


def _stopped(
    policy: RestartPolicy, stops: Stops, now: float, kind: StopKind, detail: str, *, end: bool, fail: bool
) -> Step:
    """Record a stop; at the bound the engine is `Unavailable` from this stop on."""
    kept = (*counted(policy, stops, now), now)[-policy.max_crashes :]
    if kind == "start_failed":
        logged = Log(ENGINE_START_FAILED, {"kind": kind, "detail": detail})
    else:
        logged = Log(ENGINE_STOPPED, {"kind": kind, "detail": detail})
    effects: list[Effect] = [End()] if end else []
    effects.append(logged)
    state: SupervisionState
    if len(kept) >= policy.max_crashes:
        state = Unavailable(kept, kind)
        effects.append(Log(ENGINE_UNAVAILABLE, {"stops": len(kept)}))
    else:
        state = Crashed(kept, kind)
    if fail:
        effects.append(Fail(EngineFailure(kind, detail)))
    return Step(state, tuple(effects))


def _incompatible(what: str) -> Step:
    failure = EngineFailure("incompatible", f"{what}; this sidecar speaks {PROTOCOL_VERSION}")
    return Step(
        Incompatible(failure),
        (End(), Log(ENGINE_START_FAILED, {"kind": "incompatible", "detail": failure.detail}), Fail(failure)),
    )


def _closed_failure() -> EngineFailure:
    return EngineFailure("closed", "the engine client was closed")


def _closed(*, running: bool) -> Step:
    return Step(Closed(), (*((End(),) if running else ()), Log(ENGINE_CLOSED, {})))


def _stops_of(s: SupervisionState) -> Stops:
    stops: Stops = getattr(s, "stops", ())
    return stops


def _cancelled(p: RestartPolicy, s: SupervisionState, e: Cancelled) -> Step:
    """Our end with reason `cancel` or `quit_anyway` (§5.3: outcome `cancelled`, not a stop): the engine is gone, the
    stops stay as they were, and a waiting caller fails `cancelled`. During a backoff nothing runs to end."""
    ends: tuple[Effect, ...] = () if isinstance(s, Restarting) else (End(),)
    logged = Log(ENGINE_ENDED, {"reason": e.reason})
    if isinstance(s, Ready):  # no call in flight: nobody to fail
        return Step(Idle(s.stops), (*ends, logged))
    failure = EngineFailure("cancelled", f"the engine was ended by us ({e.reason})")
    return Step(Idle(_stops_of(s)), (*ends, logged, Fail(failure)))


def _retired(p: RestartPolicy, s: Ready, e: Retired) -> Step:
    """§5.1: an engine that ran user code is ended after its final reply; §5.3: not a stop."""
    return Step(Idle(s.stops), (End(), Log(ENGINE_ENDED, {"reason": "retire"})))


def _close_in_flight(p: RestartPolicy, s: SupervisionState, e: Close) -> Step:
    """`close()` ended the engine while a call was under way: that call fails `closed`."""
    ends: tuple[Effect, ...] = () if isinstance(s, Restarting) else (End(),)
    return Step(Closed(), (*ends, Log(ENGINE_CLOSED, {}), Fail(_closed_failure())))


def _idle_request(p: RestartPolicy, s: Idle, e: Request) -> Step:
    return _start(p, counted(p, s.stops, e.now), None, e.now)


def _spawned(p: RestartPolicy, s: Starting, e: Spawned) -> Step:
    return Step(Handshaking(s.stops, e.pid, e.bytecode), (Handshake(),))


def _spawn_failed(p: RestartPolicy, s: Starting, e: SpawnFailed) -> Step:
    return _stopped(p, s.stops, e.now, "start_failed", e.detail, end=False, fail=True)


def _start_interrupted(p: RestartPolicy, s: Starting | Handshaking, e: Interrupted) -> Step:
    return _stopped(p, s.stops, e.now, "start_failed", e.detail, end=True, fail=False)


def _handshake_ok(p: RestartPolicy, s: Handshaking, e: HandshakeOk) -> Step:
    if e.info.protocol != PROTOCOL_VERSION:
        return _incompatible(f"the engine speaks protocol {e.info.protocol}")
    info = e.info
    ready = {
        "pid": s.pid,
        "protocol": info.protocol,
        "engine_version": info.engine_version,
        "beancount_version": info.beancount_version,
    }
    return Step(Ready(s.stops, s.pid, info), (Log(ENGINE_READY, ready),))


def _handshake_foreign(p: RestartPolicy, s: Handshaking, e: HandshakeForeign) -> Step:
    return _incompatible(e.detail)


def _handshake_failed(p: RestartPolicy, s: Handshaking, e: HandshakeFailed) -> Step:
    if s.bytecode:  # §5.1: a damaged `.pyc` raises instead of falling back; delete the directory, start once without
        discarded = Log(ENGINE_BYTECODE, {"outcome": "discarded", "detail": e.detail})
        return Step(Starting(s.stops), (End(), discarded, DiscardBytecode(), Spawn(bytecode=False)))
    return _stopped(p, s.stops, e.now, "start_failed", e.detail, end=True, fail=True)


def _ready_request(p: RestartPolicy, s: Ready, e: Request) -> Step:
    return Step(s)


def _send(p: RestartPolicy, s: Ready, e: Send) -> Step:
    return Step(Busy(s.stops, s.pid, s.info))


def _exited(p: RestartPolicy, s: Ready, e: Exited) -> Step:
    return _stopped(p, s.stops, e.now, "crashed", e.detail, end=True, fail=False)


def _ready_reset(p: RestartPolicy, s: Ready, e: Reset) -> Step:
    return Step(Ready((), s.pid, s.info))


def _ready_close(p: RestartPolicy, s: Ready, e: Close) -> Step:
    return _closed(running=True)


def _answered(p: RestartPolicy, s: Busy, e: Answered) -> Step:
    return Step(Ready(s.stops, s.pid, s.info))


def _ended(p: RestartPolicy, s: Busy, e: Ended) -> Step:
    return _stopped(p, s.stops, e.now, "crashed", e.detail, end=True, fail=True)


def _violated(p: RestartPolicy, s: Busy, e: Violated) -> Step:
    return _stopped(p, s.stops, e.now, "protocol_violation", e.detail, end=True, fail=True)


def _violated_past_commit(p: RestartPolicy, s: Busy, e: ViolatedPastCommit) -> Step:
    """§5.3: never signal an engine that may be in the middle of a replace; close its input and wait for its exit."""
    return Step(Finishing(s.stops, s.pid, s.info, e.detail), (CloseInput(),))


def _finished(p: RestartPolicy, s: Finishing, e: Ended) -> Step:
    """The engine ended after a violation past the commit point: the stop is counted now (§5.3)."""
    detail = f"{s.detail} (after the commit point); the engine exited"
    return _stopped(p, s.stops, e.now, "protocol_violation", detail, end=True, fail=True)


def _busy_interrupted(p: RestartPolicy, s: Busy | Finishing, e: Interrupted) -> Step:
    return _stopped(p, s.stops, e.now, "crashed", e.detail, end=True, fail=False)


def _crashed_request(p: RestartPolicy, s: Crashed, e: Request) -> Step:
    return _start(p, counted(p, s.stops, e.now), s.last, e.now)


def _backoff_elapsed(p: RestartPolicy, s: Restarting, e: BackoffElapsed) -> Step:
    return Step(Starting(s.stops), (Spawn(),))


def _backoff_interrupted(p: RestartPolicy, s: Restarting, e: Interrupted) -> Step:
    return Step(Idle(s.stops) if s.last is None else Crashed(s.stops, s.last))


def _unavailable_request(p: RestartPolicy, s: Unavailable, e: Request) -> Step:
    stops = counted(p, s.stops, e.now)
    if len(stops) >= p.max_crashes:
        detail = f"the engine stopped {len(stops)} times in {p.window:g} s; not restarting yet"
        return Step(s, (Fail(EngineFailure("unavailable", detail)),))
    return _start(p, stops, s.last, e.now)


def _incompatible_request(p: RestartPolicy, s: Incompatible, e: Request) -> Step:
    return Step(s, (Fail(s.failure),))


def _to_idle(p: RestartPolicy, s: SupervisionState, e: Reset) -> Step:
    return Step(Idle())


def _close_at_rest(p: RestartPolicy, s: SupervisionState, e: Close) -> Step:
    return _closed(running=False)


def _closed_request(p: RestartPolicy, s: Closed, e: Request) -> Step:
    return Step(s, (Fail(_closed_failure()),))


def _stay(p: RestartPolicy, s: SupervisionState, e: SupervisionEvent) -> Step:
    return Step(s)


# -- The table --------------------------------------------------------------------------------------------------------

type Handler = Callable[[RestartPolicy, Any, Any], Step]


@dataclass(frozen=True, slots=True)
class Rule:
    """One row: for `state` and `event`, the handler, the states it may lead to and the effects it may emit (the
    generated reference and the random walks read these)."""

    state: type
    event: type
    handler: Handler
    targets: tuple[type, ...]
    effects: tuple[type, ...]
    note: str


STOP = (Crashed, Unavailable)
LOGGED_STOP = (End, Log, Fail)
OURS = (End, Log, Fail)  # our own end of a call in flight: the engine ended, logged, the caller failed

# (state, event, handler, the states it may lead to, the effects it may emit, a note). A tuple of plain rows, not a
# dict, so the table is module state that cannot change (INV-11; `tests/architecture/test_globals.py`).
ROWS: tuple[tuple[type, type, Handler, tuple[type, ...], tuple[type, ...], str], ...] = (
    (Idle, Request, _idle_request, (Starting, Restarting), (Spawn, Pause), "lazy start; backoff of kept stops"),
    (Idle, Reset, _to_idle, (Idle,), (), ""),
    (Idle, Close, _close_at_rest, (Closed,), (Log,), ""),
    (Starting, Spawned, _spawned, (Handshaking,), (Handshake,), ""),
    (Starting, SpawnFailed, _spawn_failed, STOP, (Log, Fail), "a stop; start_failed"),
    (Starting, Interrupted, _start_interrupted, STOP, (End, Log), "a stop; the exception goes on"),
    (Starting, Cancelled, _cancelled, (Idle,), OURS, "no stop; nothing was spawned"),
    (Starting, Close, _close_in_flight, (Closed,), OURS, ""),
    (
        Handshaking,
        HandshakeOk,
        _handshake_ok,
        (Ready, Incompatible),
        (Log, End, Fail),
        "another number: incompatible",
    ),
    (Handshaking, HandshakeForeign, _handshake_foreign, (Incompatible,), (End, Log, Fail), "no stop"),
    (
        Handshaking,
        HandshakeFailed,
        _handshake_failed,
        (Crashed, Unavailable, Starting),
        (End, Log, Fail, DiscardBytecode, Spawn),
        "a stop; start_failed; with the bytecode directory: discard it, start once without it, no stop",
    ),
    (Handshaking, Interrupted, _start_interrupted, STOP, (End, Log), "a stop; the exception goes on"),
    (Handshaking, Cancelled, _cancelled, (Idle,), OURS, "no stop: a start that never completes"),
    (Handshaking, Close, _close_in_flight, (Closed,), OURS, ""),
    (Ready, Request, _ready_request, (Ready,), (), ""),
    (Ready, Send, _send, (Busy,), (), "the only way into Busy: never resent"),
    (Ready, Exited, _exited, STOP, (End, Log), "a stop (crashed); the request goes on"),
    (Ready, Cancelled, _cancelled, (Idle,), (End, Log), "no stop"),
    (Ready, Retired, _retired, (Idle,), (End, Log), "no stop: after a job that ran user code"),
    (Ready, Reset, _ready_reset, (Ready,), (), "stops forgotten; the engine keeps running"),
    (Ready, Close, _ready_close, (Closed,), (End, Log), ""),
    (Busy, Answered, _answered, (Ready,), (), "a result or a refusal"),
    (Busy, Ended, _ended, STOP, LOGGED_STOP, "a stop; crashed"),
    (Busy, Violated, _violated, STOP, LOGGED_STOP, "a stop; protocol_violation"),
    (Busy, ViolatedPastCommit, _violated_past_commit, (Finishing,), (CloseInput,), "nothing signalled"),
    (Busy, Interrupted, _busy_interrupted, STOP, (End, Log), "a stop (crashed); the exception goes on"),
    (Busy, Cancelled, _cancelled, (Idle,), OURS, "no stop"),
    (Busy, Close, _close_in_flight, (Closed,), OURS, ""),
    (Finishing, Ended, _finished, STOP, LOGGED_STOP, "a stop at the exit; protocol_violation"),
    (Finishing, Interrupted, _busy_interrupted, STOP, (End, Log), "a stop (crashed); the exception goes on"),
    (Finishing, Cancelled, _cancelled, (Idle,), OURS, "Quit anyway"),
    (Finishing, Close, _close_in_flight, (Closed,), OURS, ""),
    (Crashed, Request, _crashed_request, (Starting, Restarting), (Spawn, Pause), "backoff if still due"),
    (Crashed, Reset, _to_idle, (Idle,), (), ""),
    (Crashed, Close, _close_at_rest, (Closed,), (Log,), ""),
    (Restarting, BackoffElapsed, _backoff_elapsed, (Starting,), (Spawn,), ""),
    (Restarting, Interrupted, _backoff_interrupted, (Crashed, Idle), (), "no stop: nothing was started"),
    (Restarting, Cancelled, _cancelled, (Idle,), (Log, Fail), "no stop: nothing was started"),
    (Restarting, Close, _close_in_flight, (Closed,), (Log, Fail), ""),
    (
        Unavailable,
        Request,
        _unavailable_request,
        (Unavailable, Starting, Restarting),
        (Fail, Spawn, Pause),
        "refused while the bound holds in the window",
    ),
    (Unavailable, Reset, _to_idle, (Idle,), (), ""),
    (Unavailable, Close, _close_at_rest, (Closed,), (Log,), ""),
    (Incompatible, Request, _incompatible_request, (Incompatible,), (Fail,), "never retried"),
    (Incompatible, Reset, _to_idle, (Idle,), (), ""),
    (Incompatible, Close, _close_at_rest, (Closed,), (Log,), ""),
    (Closed, Request, _closed_request, (Closed,), (Fail,), "final"),
    (Closed, Reset, _stay, (Closed,), (), ""),
    (Closed, Close, _stay, (Closed,), (), ""),
)

STATES: tuple[type, ...] = (
    Idle,
    Starting,
    Handshaking,
    Ready,
    Busy,
    Finishing,
    Crashed,
    Restarting,
    Unavailable,
    Incompatible,
    Closed,
)
RESTING: tuple[type, ...] = (Idle, Ready, Crashed, Unavailable, Incompatible, Closed)
"""Where `Request` and `Reset` can find the table; the other states pass within one call."""
EVENTS: tuple[type, ...] = (
    Request,
    Spawned,
    SpawnFailed,
    HandshakeOk,
    HandshakeForeign,
    HandshakeFailed,
    BackoffElapsed,
    Send,
    Answered,
    Ended,
    Violated,
    ViolatedPastCommit,
    Exited,
    Interrupted,
    Cancelled,
    Retired,
    Reset,
    Close,
)


def rules() -> list[Rule]:
    """The table's rows as records, in order."""
    return [Rule(*row) for row in ROWS]


def rule_for(state: type, event: type) -> Rule | None:
    """The row for this pair, if the table defines one."""
    for row in ROWS:
        if row[0] is state and row[1] is event:
            return Rule(*row)
    return None


def transition(policy: RestartPolicy, state: SupervisionState, event: SupervisionEvent) -> Step:
    """The next state and the effects for the shell to carry out, in order."""
    rule = rule_for(type(state), type(event))
    if rule is None:
        raise IllegalTransition(state, event)
    return rule.handler(policy, state, event)
