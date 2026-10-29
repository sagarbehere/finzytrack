"""The engine supervision statechart (`app.platform.engine_supervision`; S0040 brief, amended by S0057; EP-17;
ARCHITECTURE.md §7; `design/specs/engine-jobs.md` §5.2-§5.5).

Every expected value comes from the S0040 brief and the S0035 supervision rules it keeps (journal S0035 "Client";
ARCHITECTURE.md §2.0 "Supervision"), as the engine-jobs spec amends them (§5.5): lazy start; backoff 0.5 s, 2 s, 8 s
by the stops in the window; three stops in 300 s make the engine unavailable until the window passes or a reset; a
stop exactly one window old no longer counts; another protocol is incompatible and never retried until a reset;
close is final; no request is resent. Since S0057: **no time limit** (no `TimedOut`); **our ends are no stops**
(§5.3: "ended by us with `cancel` or `quit_anyway`: `cancelled`, not counted"; "final reply, then our `retire` or
`close`: `answered`, not counted"), so `Cancelled`, `Retired` and `Close` end an engine anywhere it can be ended
(a start, a handshake, a backoff, a request) and keep the stops as they were; **a reply that breaks the protocol
after the commit point** closes the engine's input and waits for its exit (`Finishing`), and counts the stop then
(§5.3 table); **an engine that fails to start while using the bytecode directory** gets the directory deleted and is
started once without it, not counted (§5.1). `EXPECTED` below is the table written from those texts, not read from
the code: the exhaustive test holds the code to it.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.platform.engine import EngineFailure, EngineInfo
from app.platform.engine_supervision import EVENTS as DECLARED_EVENTS
from app.platform.engine_supervision import (
    RESTING,
    Answered,
    BackoffElapsed,
    Busy,
    Cancelled,
    Close,
    Closed,
    CloseInput,
    Crashed,
    DiscardBytecode,
    Effect,
    End,
    Ended,
    Exited,
    Fail,
    Finishing,
    Handshake,
    HandshakeFailed,
    HandshakeForeign,
    HandshakeOk,
    Handshaking,
    Idle,
    IllegalTransition,
    Incompatible,
    Interrupted,
    Log,
    Pause,
    Ready,
    Request,
    Reset,
    Restarting,
    RestartPolicy,
    Retired,
    Send,
    Spawn,
    Spawned,
    SpawnFailed,
    Starting,
    Step,
    SupervisionEvent,
    SupervisionState,
    Unavailable,
    Violated,
    ViolatedPastCommit,
    rule_for,
    rules,
    transition,
)
from app.platform.engine_supervision import STATES as DECLARED_STATES
from app.platform.observability.events import (
    ENGINE_BYTECODE,
    ENGINE_CLOSED,
    ENGINE_ENDED,
    ENGINE_READY,
    ENGINE_START_FAILED,
    ENGINE_STOPPED,
    ENGINE_UNAVAILABLE,
)

POLICY = RestartPolicy()
INFO = EngineInfo(protocol=2, engine_version="0.4.0.dev0", beancount_version="3.2.0")
OTHER_PROTOCOL = EngineInfo(protocol=3, engine_version="9", beancount_version="3.2.0")
T = 1000.0
PID = 4242

# -- The table, from the brief ------------------------------------------------------------------------------------

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
STOPPED = frozenset({Crashed, Unavailable})  # a stop lands in Crashed, or in Unavailable when it reaches the bound
EXPECTED: dict[tuple[type, type], frozenset[type]] = {
    # No engine: none started yet, after a reset, or after our own end (its stops kept); the next request starts one.
    (Idle, Request): frozenset({Starting, Restarting}),
    (Idle, Reset): frozenset({Idle}),
    (Idle, Close): frozenset({Closed}),
    # A start in progress: the process is being created. Our end of it is no stop (§5.5: "A start that never
    # completes is ended by the waiting job's Cancel").
    (Starting, Spawned): frozenset({Handshaking}),
    (Starting, SpawnFailed): STOPPED,
    (Starting, Interrupted): STOPPED,
    (Starting, Cancelled): frozenset({Idle}),
    (Starting, Close): frozenset({Closed}),
    # The handshake is out, with no time limit: our protocol, another one, no valid answer, or our end.
    (Handshaking, HandshakeOk): frozenset({Ready, Incompatible}),
    (Handshaking, HandshakeForeign): frozenset({Incompatible}),
    (Handshaking, HandshakeFailed): STOPPED | {Starting},  # Starting: once more, without the bytecode directory
    (Handshaking, Interrupted): STOPPED,
    (Handshaking, Cancelled): frozenset({Idle}),
    (Handshaking, Close): frozenset({Closed}),
    # Running and idle: a request is sent, the engine was found dead, or we ended it (Cancel, retire, close).
    (Ready, Request): frozenset({Ready}),
    (Ready, Send): frozenset({Busy}),
    (Ready, Exited): STOPPED,
    (Ready, Cancelled): frozenset({Idle}),
    (Ready, Retired): frozenset({Idle}),
    (Ready, Reset): frozenset({Ready}),
    (Ready, Close): frozenset({Closed}),
    # One request in flight, with no time limit: its answer, no valid answer, or our end.
    (Busy, Answered): frozenset({Ready}),
    (Busy, Ended): STOPPED,
    (Busy, Violated): STOPPED,
    (Busy, ViolatedPastCommit): frozenset({Finishing}),
    (Busy, Interrupted): STOPPED,
    (Busy, Cancelled): frozenset({Idle}),
    (Busy, Close): frozenset({Closed}),
    # A reply broke the protocol after the commit point: the input is closed, the output discarded until the exit.
    (Finishing, Ended): STOPPED,
    (Finishing, Interrupted): STOPPED,
    (Finishing, Cancelled): frozenset({Idle}),  # Quit anyway
    (Finishing, Close): frozenset({Closed}),
    # Stopped below the bound: the next request restarts it, after its backoff when one is due.
    (Crashed, Request): frozenset({Starting, Restarting}),
    (Crashed, Reset): frozenset({Idle}),
    (Crashed, Close): frozenset({Closed}),
    # Waiting out the backoff before a restart; our end of the waiting call ends the wait.
    (Restarting, BackoffElapsed): frozenset({Starting}),
    (Restarting, Interrupted): frozenset({Crashed, Idle}),
    (Restarting, Cancelled): frozenset({Idle}),
    (Restarting, Close): frozenset({Closed}),
    # The bound is reached: refused until the window passes (then restarted) or a reset.
    (Unavailable, Request): frozenset({Unavailable, Starting, Restarting}),
    (Unavailable, Reset): frozenset({Idle}),
    (Unavailable, Close): frozenset({Closed}),
    # Another protocol: refused until a reset.
    (Incompatible, Request): frozenset({Incompatible}),
    (Incompatible, Reset): frozenset({Idle}),
    (Incompatible, Close): frozenset({Closed}),
    # Closed is final.
    (Closed, Request): frozenset({Closed}),
    (Closed, Reset): frozenset({Closed}),
    (Closed, Close): frozenset({Closed}),
}


def samples(cls: type) -> list[SupervisionState]:
    """Instances of a state with no stops, one stop and the bound's worth of stops, where the state has stops."""
    some = ((), (T - 1.0,), (T - 3.0, T - 2.0, T - 1.0))
    out: dict[type, Sequence[SupervisionState]] = {
        Idle: [Idle(), Idle((T - 1.0,))],
        Starting: [Starting(s) for s in some[:2]] + [Starting((T - 2.0, T - 1.0))],
        Handshaking: [Handshaking(s, PID, b) for s in some[:2] for b in (False, True)],
        Ready: [Ready(s, PID, INFO) for s in some[:2]] + [Ready((T - 2.0, T - 1.0), PID, INFO)],
        Busy: [Busy(s, PID, INFO) for s in some[:2]] + [Busy((T - 2.0, T - 1.0), PID, INFO)],
        Finishing: [Finishing(s, PID, INFO, "bad reply") for s in some[:2]],
        Crashed: [Crashed((T - 1.0,), "crashed"), Crashed((T - 2.0, T - 1.0), "protocol_violation")],
        Restarting: [Restarting((T - 1.0,), "crashed"), Restarting((T - 1.0,), None)],
        Unavailable: [Unavailable(some[2], "crashed")],
        Incompatible: [Incompatible(EngineFailure("incompatible", "the engine speaks protocol 3"))],
        Closed: [Closed()],
    }
    return list(out[cls])


def event_samples(cls: type) -> list[SupervisionEvent]:
    out: dict[type, list[SupervisionEvent]] = {
        Request: [Request(T), Request(T + POLICY.window)],
        Spawned: [Spawned(PID), Spawned(PID, bytecode=True)],
        SpawnFailed: [SpawnFailed("no interpreter", T)],
        HandshakeOk: [HandshakeOk(INFO), HandshakeOk(OTHER_PROTOCOL)],
        HandshakeForeign: [HandshakeForeign("the engine speaks protocol 3")],
        HandshakeFailed: [HandshakeFailed("handshake: crashed", T)],
        BackoffElapsed: [BackoffElapsed()],
        Send: [Send()],
        Answered: [Answered()],
        Ended: [Ended("the engine's output ended", T)],
        Violated: [Violated("not a reply", T)],
        ViolatedPastCommit: [ViolatedPastCommit("not a reply")],
        Exited: [Exited("the engine exited", T)],
        Interrupted: [Interrupted("the client failed", T)],
        Cancelled: [Cancelled("cancel", T), Cancelled("quit_anyway", T)],
        Retired: [Retired()],
        Reset: [Reset()],
        Close: [Close()],
    }
    return out[cls]


def pairs() -> Iterator[tuple[type, type]]:
    for s in STATES:
        for e in EVENTS:
            yield s, e


@pytest.mark.parametrize(("state", "event"), list(pairs()), ids=lambda c: c.__name__)
def test_every_state_and_event_pair_is_defined_by_the_table_or_illegal(state: type, event: type) -> None:
    """EP-17's exhaustive (state x event) test: a pair the brief does not define raises `IllegalTransition`; a
    defined pair lands only in the states the brief lists for it."""
    for s in samples(state):
        for e in event_samples(event):
            if (state, event) not in EXPECTED:
                with pytest.raises(IllegalTransition):
                    transition(POLICY, s, e)
            else:
                step = transition(POLICY, s, e)
                assert type(step.state) in EXPECTED[state, event], (s, e, step)


def test_the_declared_table_is_the_briefs_table() -> None:
    """The table's own declaration (which the generated reference prints) has exactly the brief's rows, each once."""
    declared = [(r.state, r.event) for r in rules()]
    assert len(declared) == len(set(declared)), "a pair is listed twice"
    assert set(declared) == set(EXPECTED)
    for rule in rules():
        assert frozenset(rule.targets) == EXPECTED[rule.state, rule.event], (rule.state, rule.event)


def test_the_declared_states_and_events_are_the_briefs() -> None:
    """The generated reference lists the module's own STATES and EVENTS: they must be all of them."""
    assert set(DECLARED_STATES) == set(STATES)
    assert set(DECLARED_EVENTS) == set(EVENTS)


def test_requests_and_resets_arrive_only_in_resting_states() -> None:
    """The client's lock serializes `start`, `ping`, `call` and `reset` (S0035), so their events are defined
    exactly in the resting states. `close()` no longer waits for the lock (§5.5): its `Close` is defined everywhere,
    fed at rest by `close()` itself or, in a passing state, by the call that observes the end it signalled."""
    assert set(RESTING) == {Idle, Ready, Crashed, Unavailable, Incompatible, Closed}
    for event in (Request, Reset):
        assert {s for s, e in EXPECTED if e is event} == set(RESTING), event
    assert {s for s, e in EXPECTED if e is Close} == set(STATES)


def test_there_is_no_time_limit_left_in_the_table() -> None:
    """§5.4: "No engine job has a time limit." Nothing in the policy times a start, a handshake, a request or an
    end, and no event says a time passed."""
    fields = {f.name for f in dataclasses.fields(RestartPolicy)}
    assert fields == {"max_crashes", "window", "delays"}
    assert not any("timeout" in e.__name__.lower() or "timedout" in e.__name__.lower() for e in EVENTS)


def test_a_request_is_never_resent_because_only_a_send_enters_busy() -> None:
    """No request is ever resent (S0035): the only way into `Busy` is the caller's own `Send` from `Ready`."""
    into_busy = {key for key, targets in EXPECTED.items() if Busy in targets}
    assert into_busy == {(Ready, Send)}
    assert {(r.state, r.event) for r in rules() if Busy in r.targets} == {(Ready, Send)}


# -- Starting and the backoff -------------------------------------------------------------------------------------


def test_the_first_request_starts_the_engine_at_once() -> None:
    assert transition(POLICY, Idle(), Request(T)) == Step(Starting(()), (Spawn(),))


def test_a_spawn_may_use_the_bytecode_directory() -> None:
    assert Spawn() == Spawn(bytecode=True)


def test_a_spawned_engine_is_sent_the_handshake() -> None:
    assert transition(POLICY, Starting(()), Spawned(PID)) == Step(Handshaking((), PID, False), (Handshake(),))
    assert transition(POLICY, Starting(()), Spawned(PID, bytecode=True)) == Step(
        Handshaking((), PID, True), (Handshake(),)
    )


def test_a_handshake_in_our_protocol_makes_the_engine_ready_and_is_logged_with_its_facts() -> None:
    step = transition(POLICY, Handshaking((T - 1.0,), PID, True), HandshakeOk(INFO))
    assert step.state == Ready((T - 1.0,), PID, INFO)
    assert step.effects == (
        Log(ENGINE_READY, {"pid": PID, "protocol": 2, "engine_version": "0.4.0.dev0", "beancount_version": "3.2.0"}),
    )


@pytest.mark.parametrize(("stops", "delay"), [(1, 0.5), (2, 2.0)])
def test_a_restart_waits_the_backoff_for_the_stops_in_the_window(stops: int, delay: float) -> None:
    """0.5 s after one recent stop, 2 s after two (S0035; ARCHITECTURE.md §2.0)."""
    times = tuple(T - 0.1 * (stops - i) for i in range(stops))  # the last stop 0.1 s ago
    step = transition(POLICY, Crashed(times, "crashed"), Request(T))
    assert step.state == Restarting(times, "crashed")
    (pause,) = step.effects
    assert isinstance(pause, Pause)
    assert math.isclose(pause.seconds, delay - 0.1)
    assert transition(POLICY, step.state, BackoffElapsed()) == Step(Starting(times), (Spawn(),))


def test_after_our_own_end_the_backoff_of_earlier_stops_still_holds() -> None:
    """§5.3: "the next job starts a new engine after 0.5 s, 2 s, then 8 s by recent stops": an engine we ended in
    between does not reset that."""
    step = transition(POLICY, Idle((T - 0.1,)), Request(T))
    assert step.state == Restarting((T - 0.1,), None)
    (pause,) = step.effects
    assert isinstance(pause, Pause)
    assert math.isclose(pause.seconds, 0.4)
    assert transition(POLICY, Idle((T - 1.0,)), Request(T)) == Step(Starting((T - 1.0,)), (Spawn(),))


def test_the_third_delay_repeats_for_a_policy_that_allows_more_stops() -> None:
    policy = RestartPolicy(max_crashes=5)
    times = (T - 4.0, T - 3.0, T - 2.0, T - 1.0)
    step = transition(policy, Crashed(times, "crashed"), Request(T))
    assert step.effects == (Pause(8.0 - 1.0),)


def test_no_pause_when_the_backoff_has_already_passed() -> None:
    step = transition(POLICY, Crashed((T - 0.5,), "crashed"), Request(T))
    assert step == Step(Starting((T - 0.5,)), (Spawn(),))


def test_stops_that_left_the_window_no_longer_delay_a_restart() -> None:
    step = transition(POLICY, Crashed((T - POLICY.window,), "crashed"), Request(T))
    assert step == Step(Starting(()), (Spawn(),))


def test_an_interrupted_backoff_returns_to_rest_without_a_new_stop() -> None:
    """An exception during the pause (the caller's thread was interrupted) must leave a state a later request can
    use; nothing was started, so nothing stopped."""
    step = transition(POLICY, Restarting((T - 1.0,), "crashed"), Interrupted("KeyboardInterrupt", T))
    assert step == Step(Crashed((T - 1.0,), "crashed"), ())
    step = transition(POLICY, Restarting((T - 1.0,), None), Interrupted("KeyboardInterrupt", T))
    assert step == Step(Idle((T - 1.0,)), ())


# -- Stops ----------------------------------------------------------------------------------------------------------


STOPS_WHILE_BUSY = [
    (Ended("the engine's output ended", T), "crashed"),
    (Violated("not a reply", T), "protocol_violation"),
]


@pytest.mark.parametrize(("event", "kind"), STOPS_WHILE_BUSY)
def test_a_request_without_a_valid_answer_stops_the_engine_and_fails_the_caller(
    event: Ended | Violated, kind: str
) -> None:
    """An end we did not cause, or an invalid reply before the commit point, stops the engine (§5.3); the caller
    gets the kind."""
    step = transition(POLICY, Busy((), PID, INFO), event)
    assert step.state == Crashed((T,), kind)  # pyright: ignore[reportArgumentType]
    assert step.effects == (
        End(),
        Log(ENGINE_STOPPED, {"kind": kind, "detail": event.detail}),
        Fail(EngineFailure(kind, event.detail)),  # pyright: ignore[reportArgumentType]
    )


def test_an_answer_keeps_the_engine_running() -> None:
    assert transition(POLICY, Busy((T - 1.0,), PID, INFO), Answered()) == Step(Ready((T - 1.0,), PID, INFO), ())


def test_an_engine_found_dead_between_requests_is_a_stop_and_the_request_goes_on() -> None:
    """Noticed at the next request (S0035): it counts as a crash, and the request is not failed by it."""
    step = transition(POLICY, Ready((), PID, INFO), Exited("the engine exited", T))
    assert step.state == Crashed((T,), "crashed")
    assert step.effects == (End(), Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the engine exited"}))


def test_an_interrupted_wait_stops_the_engine_without_failing_a_caller() -> None:
    """The client's own wait failed (S0035 verifier F4c): the reply may still come, so the engine is stopped; the
    exception goes on to the caller instead of a failure."""
    step = transition(POLICY, Busy((), PID, INFO), Interrupted("the client failed while waiting", T))
    assert step.state == Crashed((T,), "crashed")
    assert step.effects == (
        End(),
        Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the client failed while waiting"}),
    )


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (Starting(()), SpawnFailed("the engine could not be started: no such file", T)),
        (Handshaking((), PID, False), HandshakeFailed("handshake: crashed: the engine's output ended", T)),
    ],
)
def test_a_start_that_fails_is_a_stop_and_fails_the_caller_as_start_failed(
    state: SupervisionState, event: SpawnFailed | HandshakeFailed
) -> None:
    step = transition(POLICY, state, event)
    assert step.state == Crashed((T,), "start_failed")
    ends = (End(),) if isinstance(state, Handshaking) else ()
    assert step.effects == (
        *ends,
        Log(ENGINE_START_FAILED, {"kind": "start_failed", "detail": event.detail}),
        Fail(EngineFailure("start_failed", event.detail)),
    )


@pytest.mark.parametrize("state", [Starting(()), Handshaking((), PID, False)])
def test_an_interrupted_start_is_a_stop_and_ends_what_was_started(state: SupervisionState) -> None:
    step = transition(POLICY, state, Interrupted("the client failed while starting", T))
    assert step.state == Crashed((T,), "start_failed")
    assert step.effects == (
        End(),
        Log(ENGINE_START_FAILED, {"kind": "start_failed", "detail": "the client failed while starting"}),
    )


# -- The bytecode directory (§5.1) ----------------------------------------------------------------------------------


def test_a_start_that_fails_with_the_bytecode_directory_discards_it_and_starts_once_without_it() -> None:
    """§5.1: "If an engine fails to start while using the directory, the directory is deleted and the engine is
    started once without it". A damaged `.pyc` is not the engine's crash: no stop."""
    step = transition(POLICY, Handshaking((T - 1.0,), PID, True), HandshakeFailed("bad marshal data", T))
    assert step.state == Starting((T - 1.0,))
    assert step.effects == (
        End(),
        Log(ENGINE_BYTECODE, {"outcome": "discarded", "detail": "bad marshal data"}),
        DiscardBytecode(),
        Spawn(bytecode=False),
    )


def test_a_start_that_fails_again_without_the_directory_is_an_ordinary_failed_start() -> None:
    step = transition(POLICY, Starting((T - 1.0,)), Spawned(PID, bytecode=False))
    step = transition(POLICY, step.state, HandshakeFailed("still broken", T))
    assert step.state == Crashed((T - 1.0, T), "start_failed")
    assert Fail(EngineFailure("start_failed", "still broken")) in step.effects


# -- Our own ends: Cancel, Quit anyway, retire, close (§5.2, §5.3) ---------------------------------------------------

PASSING_WITH_A_CALLER = [
    Restarting((T - 1.0,), "crashed"),
    Starting((T - 1.0,)),
    Handshaking((T - 1.0,), PID, False),
    Busy((T - 1.0,), PID, INFO),
    Finishing((T - 1.0,), PID, INFO, "bad reply"),
]


@pytest.mark.parametrize("state", PASSING_WITH_A_CALLER)
@pytest.mark.parametrize("reason", ["cancel", "quit_anyway"])
def test_a_cancel_ends_any_call_in_flight_and_is_no_stop(state: SupervisionState, reason: str) -> None:
    """§5.3: "ended by us with `cancel` or `quit_anyway`: outcome `cancelled`; counted as a stop: no". A start that
    never completes is ended this way too (§5.5)."""
    step = transition(POLICY, state, Cancelled(reason, T))  # type: ignore[arg-type]
    assert step.state == Idle((T - 1.0,))  # the earlier stop kept, none added
    ends = () if isinstance(state, Restarting) else (End(),)  # nothing runs during a backoff
    assert step.effects == (
        *ends,
        Log(ENGINE_ENDED, {"reason": reason}),
        Fail(EngineFailure("cancelled", f"the engine was ended by us ({reason})")),
    )


@pytest.mark.parametrize("reason", ["cancel", "quit_anyway"])
def test_a_cancel_of_an_idle_engine_ends_it_with_no_caller_to_fail(reason: str) -> None:
    step = transition(POLICY, Ready((T - 1.0,), PID, INFO), Cancelled(reason, T))  # type: ignore[arg-type]
    assert step == Step(Idle((T - 1.0,)), (End(), Log(ENGINE_ENDED, {"reason": reason})))


def test_a_retire_ends_an_idle_engine_and_is_no_stop() -> None:
    """§5.1: "An engine that ran user code is ended after its job"; §5.3: "final reply, then our `retire`:
    `answered`, not counted"."""
    step = transition(POLICY, Ready((T - 1.0,), PID, INFO), Retired())
    assert step == Step(Idle((T - 1.0,)), (End(), Log(ENGINE_ENDED, {"reason": "retire"})))
    assert transition(POLICY, step.state, Request(T + 10.0)) == Step(Starting((T - 1.0,)), (Spawn(),))


def test_three_cancels_in_a_row_leave_the_engine_startable_at_once() -> None:
    """§14: "three Cancels in a row leave the engine `Ready`, not `Unavailable`"."""
    state: SupervisionState = Idle()
    now = T
    for _ in range(3):
        step = transition(POLICY, state, Request(now))
        assert step == Step(Starting(()), (Spawn(),))
        state = transition(POLICY, step.state, Spawned(PID)).state
        state = transition(POLICY, state, HandshakeOk(INFO)).state
        state = transition(POLICY, state, Send()).state
        state = transition(POLICY, state, Cancelled("cancel", now)).state
        assert state == Idle(())
        now += 0.01
    assert transition(POLICY, state, Request(now)) == Step(Starting(()), (Spawn(),))


@pytest.mark.parametrize("state", PASSING_WITH_A_CALLER)
def test_a_close_observed_during_a_call_ends_it_as_closed(state: SupervisionState) -> None:
    step = transition(POLICY, state, Close())
    ends = () if isinstance(state, Restarting) else (End(),)
    assert step == Step(
        Closed(), (*ends, Log(ENGINE_CLOSED, {}), Fail(EngineFailure("closed", "the engine client was closed")))
    )


def test_our_ends_never_count_as_stops_in_any_state() -> None:
    for (state_cls, event_cls), _ in EXPECTED.items():
        if event_cls not in (Cancelled, Retired, Close):
            continue
        for s in samples(state_cls):
            for e in event_samples(event_cls):
                step = transition(POLICY, s, e)
                logged = {eff.event for eff in step.effects if isinstance(eff, Log)}
                assert not logged & {ENGINE_STOPPED, ENGINE_START_FAILED, ENGINE_UNAVAILABLE}, (s, e, step)
                if not isinstance(step.state, Closed):  # closed is final: no stops are kept there
                    assert getattr(step.state, "stops", ()) == getattr(s, "stops", ()), (s, e, step)


# -- A violation after the commit point (§5.3 table) ------------------------------------------------------------------


def test_a_violation_after_the_commit_point_closes_the_input_and_signals_nothing() -> None:
    """§5.3: "a reply that breaks the protocol after the commit point: close the engine's input, read and discard
    every further byte, wait for the exit, resolve from disk; counted as a stop at the exit". Ending the engine there
    could cut a replace; letting it run lets the save finish or roll itself back (INV-01)."""
    step = transition(POLICY, Busy((T - 1.0,), PID, INFO), ViolatedPastCommit("a second gate"))
    assert step == Step(Finishing((T - 1.0,), PID, INFO, "a second gate"), (CloseInput(),))
    assert not any(isinstance(e, End) for e in step.effects)


def test_the_violations_stop_is_counted_at_the_exit() -> None:
    step = transition(POLICY, Finishing((T - 1.0,), PID, INFO, "a second gate"), Ended("the output ended", T))
    detail = "a second gate (after the commit point); the engine exited"
    assert step == Step(
        Crashed((T - 1.0, T), "protocol_violation"),
        (
            End(),
            Log(ENGINE_STOPPED, {"kind": "protocol_violation", "detail": detail}),
            Fail(EngineFailure("protocol_violation", detail)),
        ),
    )


# -- The bound and its window -------------------------------------------------------------------------------------


def test_the_stop_that_reaches_the_bound_makes_the_engine_unavailable_at_once() -> None:
    """Three stops in 300 s (S0035). Decided in S0040: the state is `Unavailable` from that stop on, not from the
    next request, so a reader of the state (the future /health) sees it at once. The caller of the stopping
    request still gets its own failure; `engine.unavailable` is logged once, here."""
    stops = (T - 20.0, T - 10.0)
    step = transition(POLICY, Busy(stops, PID, INFO), Ended("the engine's output ended", T))
    assert step.state == Unavailable((*stops, T), "crashed")
    assert step.effects == (
        End(),
        Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the engine's output ended"}),
        Log(ENGINE_UNAVAILABLE, {"stops": 3}),
        Fail(EngineFailure("crashed", "the engine's output ended")),
    )


def test_stops_outside_the_window_do_not_count_towards_the_bound() -> None:
    stops = (T - POLICY.window, T - 10.0)  # the first is exactly one window old: out
    step = transition(POLICY, Busy(stops, PID, INFO), Ended("exit 9", T))
    assert step.state == Crashed((T - 10.0, T), "crashed")


def test_an_unavailable_engine_refuses_until_the_window_passes_and_logs_nothing_more() -> None:
    stops = (T - 3.0, T - 2.0, T - 1.0)
    step = transition(POLICY, Unavailable(stops, "crashed"), Request(T))
    assert step.state == Unavailable(stops, "crashed")
    (fail,) = step.effects
    assert isinstance(fail, Fail)
    assert fail.failure.kind == "unavailable"
    assert "3 times in 300 s" in fail.failure.detail


def test_an_unavailable_engine_still_refuses_just_inside_the_window() -> None:
    stops = (T - 3.0, T - 2.0, T - 1.0)
    step = transition(POLICY, Unavailable(stops, "crashed"), Request(T - 3.0 + POLICY.window - 0.001))
    assert isinstance(step.state, Unavailable)


def test_an_unavailable_engine_is_restarted_once_its_oldest_stop_left_the_window() -> None:
    """A stop exactly one window old no longer counts (S0035 independent verifier F8, M2): two are left, so a start
    is tried, after the backoff for two stops if it is still due."""
    stops = (T, T + 1.0, T + 2.0)
    now = T + POLICY.window
    step = transition(POLICY, Unavailable(stops, "start_failed"), Request(now))
    assert step == Step(Starting((T + 1.0, T + 2.0)), (Spawn(),))  # the 2 s backoff from T + 2 has passed


def test_an_unavailable_engine_whose_stops_all_left_the_window_starts_at_once() -> None:
    stops = (T, T + 1.0, T + 2.0)
    step = transition(POLICY, Unavailable(stops, "crashed"), Request(T + 2.0 + POLICY.window))
    assert step == Step(Starting(()), (Spawn(),))


def test_a_failed_start_reaching_the_bound_is_unavailable_and_fails_as_start_failed() -> None:
    step = transition(POLICY, Starting((T - 2.0, T - 1.0)), SpawnFailed("no interpreter", T))
    assert step.state == Unavailable((T - 2.0, T - 1.0, T), "start_failed")
    assert step.effects[-1] == Fail(EngineFailure("start_failed", "no interpreter"))
    assert Log(ENGINE_UNAVAILABLE, {"stops": 3}) in step.effects


def test_a_policy_with_a_bound_of_one_is_unavailable_after_the_first_stop() -> None:
    step = transition(RestartPolicy(max_crashes=1), Busy((), PID, INFO), Ended("gone", T))
    assert step.state == Unavailable((T,), "crashed")


# -- Incompatible, reset and close --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "what"),
    [
        (HandshakeForeign("the engine speaks protocol 3"), "the engine speaks protocol 3"),
        (HandshakeOk(OTHER_PROTOCOL), "the engine speaks protocol 3"),
    ],
)
def test_another_protocol_is_incompatible_ended_and_no_stop(event: SupervisionEvent, what: str) -> None:
    step = transition(POLICY, Handshaking((T - 1.0,), PID, True), event)
    failure = EngineFailure("incompatible", f"{what}; this sidecar speaks 2")
    assert step == Step(
        Incompatible(failure),
        (End(), Log(ENGINE_START_FAILED, {"kind": "incompatible", "detail": failure.detail}), Fail(failure)),
    )


def test_an_incompatible_engine_is_never_started_again_until_a_reset() -> None:
    failure = EngineFailure("incompatible", "the engine speaks protocol 3; this sidecar speaks 2")
    state = Incompatible(failure)
    for now in (T, T + POLICY.window * 10):
        assert transition(POLICY, state, Request(now)) == Step(state, (Fail(failure),))
    assert transition(POLICY, state, Reset()) == Step(Idle(), ())


@pytest.mark.parametrize(
    "state",
    [Crashed((T - 1.0,), "crashed"), Unavailable((T - 3.0, T - 2.0, T - 1.0), "crashed"), Idle(), Idle((T - 1.0,))],
)
def test_a_reset_forgets_past_stops_so_the_next_request_starts_at_once(state: SupervisionState) -> None:
    step = transition(POLICY, state, Reset())
    assert step == Step(Idle(), ())
    assert transition(POLICY, step.state, Request(T)) == Step(Starting(()), (Spawn(),))


def test_a_reset_keeps_a_running_engine_and_forgets_its_stops() -> None:
    assert transition(POLICY, Ready((T - 1.0,), PID, INFO), Reset()) == Step(Ready((), PID, INFO), ())


def test_a_ready_engine_answers_a_request_without_effects() -> None:
    assert transition(POLICY, Ready((), PID, INFO), Request(T)) == Step(Ready((), PID, INFO), ())
    assert transition(POLICY, Ready((), PID, INFO), Send()) == Step(Busy((), PID, INFO), ())


def test_closing_a_running_engine_ends_it_and_is_logged() -> None:
    assert transition(POLICY, Ready((), PID, INFO), Close()) == Step(Closed(), (End(), Log(ENGINE_CLOSED, {})))


@pytest.mark.parametrize(
    "state",
    [
        Idle(),
        Crashed((T,), "crashed"),
        Unavailable((T, T, T), "crashed"),
        Incompatible(EngineFailure("incompatible", "x")),
    ],
)
def test_closing_without_an_engine_is_logged_and_ends_nothing(state: SupervisionState) -> None:
    assert transition(POLICY, state, Close()) == Step(Closed(), (Log(ENGINE_CLOSED, {}),))


def test_closed_is_final() -> None:
    step = transition(POLICY, Closed(), Request(T))
    assert step == Step(Closed(), (Fail(EngineFailure("closed", "the engine client was closed")),))
    assert transition(POLICY, Closed(), Reset()) == Step(Closed(), ())
    assert transition(POLICY, Closed(), Close()) == Step(Closed(), ())  # closing twice logs once


# -- Random walks (EP-17) -----------------------------------------------------------------------------------------

RUNNING = (Handshaking, Ready, Busy, Finishing)
FOLLOW_UPS = (Spawn, Handshake, Pause)
OURS = (Cancelled, Retired, Close)


@dataclasses.dataclass
class World:
    """The shell around the table, as a model: a clock, whether an engine process exists, and the steps taken."""

    policy: RestartPolicy
    state: SupervisionState = dataclasses.field(default_factory=Idle)
    now: float = T
    alive: bool = False
    pending: Effect | None = None
    ever_closed: bool = False

    def feed(self, event: SupervisionEvent) -> Step:
        before = self.state
        rule = rule_for(type(before), type(event))
        assert rule is not None, (before, event)
        step = transition(self.policy, before, event)
        check_step(self, before, event, step, rule.targets, rule.effects)
        self.state = step.state
        self.pending = None
        for effect in step.effects:
            if isinstance(effect, End):
                self.alive = False
            elif isinstance(effect, FOLLOW_UPS):
                self.pending = effect
        check_world(self)
        return step


def stop_delay(policy: RestartPolicy, n: int) -> float:
    return policy.delays[min(n, len(policy.delays)) - 1] if policy.delays and n else 0.0


def check_step(
    world: World,
    before: SupervisionState,
    event: SupervisionEvent,
    step: Step,
    targets: tuple[type, ...],
    effects: tuple[type, ...],
) -> None:
    assert type(step.state) in targets, (before, step)
    assert {type(e) for e in step.effects} <= set(effects), (before, step)
    fails = [e for e in step.effects if isinstance(e, Fail)]
    follow = [e for e in step.effects if isinstance(e, FOLLOW_UPS)]
    assert len(fails) <= 1
    assert len(follow) <= 1
    assert not (fails and follow), f"a step both failed the caller and started work: {step}"
    for f in fails:
        expected_state = {"unavailable": Unavailable, "incompatible": Incompatible, "closed": Closed}.get(
            f.failure.kind
        )
        if expected_state is not None:
            assert isinstance(step.state, expected_state), step
    if isinstance(event, OURS) and not isinstance(step.state, Closed):  # §5.3: our ends are never stops
        assert getattr(step.state, "stops", ()) == getattr(before, "stops", ()), (before, event, step)
    if isinstance(before, Finishing) and any(isinstance(e, End) for e in step.effects):
        # past the commit point only the exit, Quit anyway, close or our own exception end the engine
        assert isinstance(event, (Ended, Cancelled, Close, Interrupted)), (before, event, step)
    if any(isinstance(e, Spawn) for e in step.effects):
        assert isinstance(step.state, Starting)
        assert not isinstance(before, (Incompatible, Closed))
        stops = step.state.stops
        assert len(stops) < world.policy.max_crashes, f"a spawn with the bound reached: {step}"
        if stops and not isinstance(before, Handshaking):  # the bytecode retry is part of the same start
            due = stops[-1] + stop_delay(world.policy, len(stops))
            assert world.now >= due - 1e-6, f"a spawn before its backoff: now {world.now}, due {due}"


def check_world(world: World) -> None:
    state = world.state
    assert world.alive == isinstance(state, RUNNING), f"engine process alive={world.alive} in {state}"
    stops: tuple[float, ...] = getattr(state, "stops", ())
    assert len(stops) <= world.policy.max_crashes
    assert list(stops) == sorted(stops)
    if isinstance(state, Unavailable):
        assert len(stops) == world.policy.max_crashes
    if world.ever_closed:
        assert isinstance(state, Closed), "closed is final"
    if isinstance(state, Closed):
        world.ever_closed = True


policies = st.builds(
    RestartPolicy,
    max_crashes=st.integers(1, 4),
    window=st.sampled_from([5.0, 30.0, 300.0]),
    delays=st.sampled_from([(), (0.5,), (0.5, 2.0, 8.0), (0.0, 1.0)]),
)


@settings(max_examples=300, deadline=None, derandomize=True)
@given(policy=policies, data=st.data())
def test_random_walks_keep_the_supervision_invariants(policy: RestartPolicy, data: st.DataObject) -> None:
    """Properties (S0040 brief, S0057): no spawn in Incompatible or Closed or with the bound reached; every spawn
    after a stop waits out its backoff; at most `max_crashes` stops kept; no step both fails the caller and starts
    work; an engine process exists exactly in Handshaking, Ready, Busy and Finishing; Unavailable holds the bound's
    worth of stops; Closed is final; our own ends never add a stop; past the commit point nothing but the exit, Quit
    anyway, close or our own exception ends the engine; every state and effect is one its table row declares."""
    world = World(policy)
    ours: list[SupervisionEvent] = [Cancelled("cancel", T), Cancelled("quit_anyway", T), Close()]
    for _ in range(data.draw(st.integers(1, 60))):
        world.now += data.draw(st.sampled_from([0.0, 0.1, 1.0, 5.0, 100.0, 301.0]))
        state, pending = world.state, world.pending
        if isinstance(pending, Spawn):
            outcome = data.draw(st.sampled_from(["spawned", "failed", "interrupted", "interrupted_after", "ours"]))
            if outcome in ("spawned", "interrupted_after"):
                world.alive = True
            if outcome == "spawned":
                world.feed(Spawned(PID, bytecode=pending.bytecode and data.draw(st.booleans())))
            elif outcome == "failed":
                world.feed(SpawnFailed("no interpreter", world.now))
            elif outcome == "ours":
                world.feed(data.draw(st.sampled_from(ours)))
            else:
                world.feed(Interrupted("interrupted", world.now))
        elif isinstance(pending, Handshake):
            world.feed(
                data.draw(
                    st.sampled_from(
                        [
                            HandshakeOk(INFO),
                            HandshakeOk(OTHER_PROTOCOL),
                            HandshakeForeign("protocol 3"),
                            HandshakeFailed("handshake: crashed", world.now),
                            Interrupted("interrupted", world.now),
                            *ours,
                        ]
                    )
                )
            )
        elif isinstance(pending, Pause):
            choice = data.draw(st.sampled_from(["elapsed", "interrupted", "ours"]))
            if choice == "elapsed":
                world.now += pending.seconds
                world.feed(BackoffElapsed())
            elif choice == "ours":
                world.feed(data.draw(st.sampled_from(ours)))
            else:
                world.feed(Interrupted("interrupted", world.now))
        elif isinstance(state, Busy):
            world.feed(
                data.draw(
                    st.sampled_from(
                        [
                            Answered(),
                            Ended("e", world.now),
                            Violated("v", world.now),
                            ViolatedPastCommit("v"),
                            Interrupted("i", world.now),
                            *ours,
                        ]
                    )
                )
            )
        elif isinstance(state, Finishing):
            world.feed(data.draw(st.sampled_from([Ended("exit", world.now), Interrupted("i", world.now), *ours])))
        else:  # a resting state: the public calls (and, while running, the engine dying on its own or our end)
            choices: list[SupervisionEvent] = [Request(world.now), Reset(), Close()]
            if isinstance(state, Ready):
                choices += [Send(), Exited("exit 9", world.now), Retired(), *ours]
            event = data.draw(st.sampled_from(choices))
            if isinstance(event, Exited):
                world.alive = False  # it died by itself; the table's End drains and reaps it
            world.feed(event)
