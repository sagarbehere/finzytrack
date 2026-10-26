"""The engine supervision statechart (`app.platform.engine_supervision`; S0040 brief; EP-17; ARCHITECTURE.md §7).

Every expected value comes from the S0040 brief and the S0035 supervision rules it keeps (journal S0035 "Client";
ARCHITECTURE.md §2.0 "Supervision"): lazy start; backoff 0.5 s, 2 s, 8 s by the stops in the window; three stops in
300 s make the engine unavailable until the window passes or a reset; a stop exactly one window old no longer counts;
another protocol is incompatible and never retried until a reset; close is final; no request is resent. `EXPECTED`
below is the table written from the brief, not read from the code: the exhaustive test holds the code to it.
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
    Close,
    Closed,
    Crashed,
    Effect,
    Ended,
    Exited,
    Fail,
    Handshake,
    HandshakeFailed,
    HandshakeForeign,
    HandshakeOk,
    Handshaking,
    Idle,
    IllegalTransition,
    Incompatible,
    Interrupted,
    Kill,
    Log,
    Pause,
    Ready,
    Request,
    Reset,
    Restarting,
    RestartPolicy,
    Send,
    Shutdown,
    Spawn,
    Spawned,
    SpawnFailed,
    Starting,
    Step,
    SupervisionEvent,
    SupervisionState,
    TimedOut,
    Unavailable,
    Violated,
    rule_for,
    rules,
    transition,
)
from app.platform.engine_supervision import STATES as DECLARED_STATES
from app.platform.observability.events import (
    ENGINE_CLOSED,
    ENGINE_READY,
    ENGINE_START_FAILED,
    ENGINE_STOPPED,
    ENGINE_UNAVAILABLE,
)

POLICY = RestartPolicy()
INFO = EngineInfo(protocol=1, engine_version="0.4.0.dev0", beancount_version="3.2.0")
OTHER_PROTOCOL = EngineInfo(protocol=2, engine_version="9", beancount_version="3.2.0")
T = 1000.0
PID = 4242

# -- The table, from the brief ------------------------------------------------------------------------------------

STATES: tuple[type, ...] = (
    Idle,
    Starting,
    Handshaking,
    Ready,
    Busy,
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
    TimedOut,
    Ended,
    Violated,
    Exited,
    Interrupted,
    Reset,
    Close,
)
STOPPED = frozenset({Crashed, Unavailable})  # a stop lands in Crashed, or in Unavailable when it reaches the bound
EXPECTED: dict[tuple[type, type], frozenset[type]] = {
    # No engine, none started yet (or after a reset): the next request starts one.
    (Idle, Request): frozenset({Starting}),
    (Idle, Reset): frozenset({Idle}),
    (Idle, Close): frozenset({Closed}),
    # A start in progress: the process is being created.
    (Starting, Spawned): frozenset({Handshaking}),
    (Starting, SpawnFailed): STOPPED,
    (Starting, Interrupted): STOPPED,
    # The handshake is out: our protocol, another one, or no valid answer.
    (Handshaking, HandshakeOk): frozenset({Ready, Incompatible}),
    (Handshaking, HandshakeForeign): frozenset({Incompatible}),
    (Handshaking, HandshakeFailed): STOPPED,
    (Handshaking, Interrupted): STOPPED,
    # Running and idle: a request is sent, or the engine was found dead between requests.
    (Ready, Request): frozenset({Ready}),
    (Ready, Send): frozenset({Busy}),
    (Ready, Exited): STOPPED,
    (Ready, Reset): frozenset({Ready}),
    (Ready, Close): frozenset({Closed}),
    # One request in flight: its answer (a result or a refusal), or no valid answer.
    (Busy, Answered): frozenset({Ready}),
    (Busy, TimedOut): STOPPED,
    (Busy, Ended): STOPPED,
    (Busy, Violated): STOPPED,
    (Busy, Interrupted): STOPPED,
    # Stopped below the bound: the next request restarts it, after its backoff when one is due.
    (Crashed, Request): frozenset({Starting, Restarting}),
    (Crashed, Reset): frozenset({Idle}),
    (Crashed, Close): frozenset({Closed}),
    # Waiting out the backoff before a restart.
    (Restarting, BackoffElapsed): frozenset({Starting}),
    (Restarting, Interrupted): frozenset({Crashed}),
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
        Idle: [Idle()],
        Starting: [Starting(s) for s in some[:2]] + [Starting((T - 2.0, T - 1.0))],
        Handshaking: [Handshaking(s, PID) for s in some[:2]],
        Ready: [Ready(s, PID, INFO) for s in some[:2]] + [Ready((T - 2.0, T - 1.0), PID, INFO)],
        Busy: [Busy(s, PID, INFO) for s in some[:2]] + [Busy((T - 2.0, T - 1.0), PID, INFO)],
        Crashed: [Crashed((T - 1.0,), "crashed"), Crashed((T - 2.0, T - 1.0), "timeout")],
        Restarting: [Restarting((T - 1.0,), "crashed")],
        Unavailable: [Unavailable(some[2], "crashed")],
        Incompatible: [Incompatible(EngineFailure("incompatible", "the engine speaks protocol 2"))],
        Closed: [Closed()],
    }
    return list(out[cls])


def event_samples(cls: type) -> list[SupervisionEvent]:
    out: dict[type, list[SupervisionEvent]] = {
        Request: [Request(T), Request(T + POLICY.window)],
        Spawned: [Spawned(PID)],
        SpawnFailed: [SpawnFailed("no interpreter", T)],
        HandshakeOk: [HandshakeOk(INFO), HandshakeOk(OTHER_PROTOCOL)],
        HandshakeForeign: [HandshakeForeign("the engine speaks protocol 2")],
        HandshakeFailed: [HandshakeFailed("handshake: timeout", T)],
        BackoffElapsed: [BackoffElapsed()],
        Send: [Send()],
        Answered: [Answered()],
        TimedOut: [TimedOut("no reply", T)],
        Ended: [Ended("exited with code 9", T)],
        Violated: [Violated("not a reply", T)],
        Exited: [Exited("exited with code 9", T)],
        Interrupted: [Interrupted("the client failed", T)],
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


def test_public_calls_arrive_only_in_resting_states() -> None:
    """The client's lock serializes `start`, `ping`, `call`, `reset` and `close` (S0035), so their events are defined
    exactly in the resting states, and illegal while a start, a backoff or a request is under way."""
    assert set(RESTING) == {Idle, Ready, Crashed, Unavailable, Incompatible, Closed}
    for event in (Request, Reset, Close):
        assert {s for s, e in EXPECTED if e is event} == set(RESTING), event


def test_a_request_is_never_resent_because_only_a_send_enters_busy() -> None:
    """No request is ever resent (S0035): the only way into `Busy` is the caller's own `Send` from `Ready`."""
    into_busy = {key for key, targets in EXPECTED.items() if Busy in targets}
    assert into_busy == {(Ready, Send)}
    assert {(r.state, r.event) for r in rules() if Busy in r.targets} == {(Ready, Send)}


# -- Starting and the backoff -------------------------------------------------------------------------------------


def test_the_first_request_starts_the_engine_at_once() -> None:
    assert transition(POLICY, Idle(), Request(T)) == Step(Starting(()), (Spawn(),))


def test_a_spawned_engine_is_sent_the_handshake() -> None:
    assert transition(POLICY, Starting(()), Spawned(PID)) == Step(Handshaking((), PID), (Handshake(),))


def test_a_handshake_in_our_protocol_makes_the_engine_ready_and_is_logged_with_its_facts() -> None:
    step = transition(POLICY, Handshaking((T - 1.0,), PID), HandshakeOk(INFO))
    assert step.state == Ready((T - 1.0,), PID, INFO)
    assert step.effects == (
        Log(ENGINE_READY, {"pid": PID, "protocol": 1, "engine_version": "0.4.0.dev0", "beancount_version": "3.2.0"}),
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


def test_the_third_delay_repeats_for_a_policy_that_allows_more_stops() -> None:
    policy = RestartPolicy(max_crashes=5)
    times = (T - 4.0, T - 3.0, T - 2.0, T - 1.0)
    step = transition(policy, Crashed(times, "crashed"), Request(T))
    assert step.effects == (Pause(8.0 - 1.0),)


def test_no_pause_when_the_backoff_has_already_passed() -> None:
    step = transition(POLICY, Crashed((T - 0.5,), "timeout"), Request(T))
    assert step == Step(Starting((T - 0.5,)), (Spawn(),))


def test_stops_that_left_the_window_no_longer_delay_a_restart() -> None:
    step = transition(POLICY, Crashed((T - POLICY.window,), "crashed"), Request(T))
    assert step == Step(Starting(()), (Spawn(),))


def test_an_interrupted_backoff_returns_to_crashed_without_a_new_stop() -> None:
    """An exception during the pause (the caller's thread was interrupted) must leave a state a later request can
    use; nothing was started, so nothing stopped."""
    step = transition(POLICY, Restarting((T - 1.0,), "timeout"), Interrupted("KeyboardInterrupt", T))
    assert step == Step(Crashed((T - 1.0,), "timeout"), ())


# -- Stops ----------------------------------------------------------------------------------------------------------


STOPS_WHILE_BUSY = [
    (TimedOut("no reply to ping within 0.5 s", T), "timeout"),
    (Ended("the engine exited with code 9", T), "crashed"),
    (Violated("not a reply", T), "protocol_violation"),
]


@pytest.mark.parametrize(("event", "kind"), STOPS_WHILE_BUSY)
def test_a_request_without_a_valid_answer_stops_the_engine_and_fails_the_caller(
    event: TimedOut | Ended | Violated, kind: str
) -> None:
    """A timeout or an invalid reply stops the engine (its state is unknown; S0035); the caller gets the kind."""
    step = transition(POLICY, Busy((), PID, INFO), event)
    assert step.state == Crashed((T,), kind)  # pyright: ignore[reportArgumentType]
    assert step.effects == (
        Kill(),
        Log(ENGINE_STOPPED, {"kind": kind, "detail": event.detail}),
        Fail(EngineFailure(kind, event.detail)),  # pyright: ignore[reportArgumentType]
    )


def test_an_answer_keeps_the_engine_running() -> None:
    assert transition(POLICY, Busy((T - 1.0,), PID, INFO), Answered()) == Step(Ready((T - 1.0,), PID, INFO), ())


def test_an_engine_found_dead_between_requests_is_a_stop_and_the_request_goes_on() -> None:
    """Noticed at the next request (S0035): it counts as a crash, and the request is not failed by it."""
    step = transition(POLICY, Ready((), PID, INFO), Exited("the engine exited with code 9", T))
    assert step.state == Crashed((T,), "crashed")
    assert step.effects == (Kill(), Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the engine exited with code 9"}))


def test_an_interrupted_wait_stops_the_engine_without_failing_a_caller() -> None:
    """The client's own wait failed (S0035 verifier F4c): the reply may still come, so the engine is stopped; the
    exception goes on to the caller instead of a failure."""
    step = transition(POLICY, Busy((), PID, INFO), Interrupted("the client failed while waiting", T))
    assert step.state == Crashed((T,), "crashed")
    assert step.effects == (
        Kill(),
        Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the client failed while waiting"}),
    )


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (Starting(()), SpawnFailed("the engine could not be started: no such file", T)),
        (Handshaking((), PID), HandshakeFailed("handshake: timeout: no reply", T)),
    ],
)
def test_a_start_that_fails_is_a_stop_and_fails_the_caller_as_start_failed(
    state: SupervisionState, event: SpawnFailed | HandshakeFailed
) -> None:
    step = transition(POLICY, state, event)
    assert step.state == Crashed((T,), "start_failed")
    kills = (Kill(),) if isinstance(state, Handshaking) else ()
    assert step.effects == (
        *kills,
        Log(ENGINE_START_FAILED, {"kind": "start_failed", "detail": event.detail}),
        Fail(EngineFailure("start_failed", event.detail)),
    )


@pytest.mark.parametrize("state", [Starting(()), Handshaking((), PID)])
def test_an_interrupted_start_is_a_stop_and_kills_what_was_started(state: SupervisionState) -> None:
    step = transition(POLICY, state, Interrupted("the client failed while starting", T))
    assert step.state == Crashed((T,), "start_failed")
    assert step.effects == (
        Kill(),
        Log(ENGINE_START_FAILED, {"kind": "start_failed", "detail": "the client failed while starting"}),
    )


# -- The bound and its window -------------------------------------------------------------------------------------


def test_the_stop_that_reaches_the_bound_makes_the_engine_unavailable_at_once() -> None:
    """Three stops in 300 s (S0035). Decided in S0040: the state is `Unavailable` from that stop on, not from the
    next request, so a reader of the state (the future /health) sees it at once. The caller of the stopping
    request still gets its own failure; `engine.unavailable` is logged once, here."""
    stops = (T - 20.0, T - 10.0)
    step = transition(POLICY, Busy(stops, PID, INFO), Ended("the engine exited with code 9", T))
    assert step.state == Unavailable((*stops, T), "crashed")
    assert step.effects == (
        Kill(),
        Log(ENGINE_STOPPED, {"kind": "crashed", "detail": "the engine exited with code 9"}),
        Log(ENGINE_UNAVAILABLE, {"stops": 3}),
        Fail(EngineFailure("crashed", "the engine exited with code 9")),
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
    step = transition(RestartPolicy(max_crashes=1), Busy((), PID, INFO), TimedOut("no reply", T))
    assert step.state == Unavailable((T,), "timeout")


# -- Incompatible, reset and close --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "what"),
    [
        (HandshakeForeign("the engine speaks protocol 2"), "the engine speaks protocol 2"),
        (HandshakeOk(OTHER_PROTOCOL), "the engine speaks protocol 2"),
    ],
)
def test_another_protocol_is_incompatible_shut_down_and_no_stop(event: SupervisionEvent, what: str) -> None:
    step = transition(POLICY, Handshaking((T - 1.0,), PID), event)
    failure = EngineFailure("incompatible", f"{what}; this sidecar speaks 1")
    assert step == Step(
        Incompatible(failure),
        (Shutdown(), Log(ENGINE_START_FAILED, {"kind": "incompatible", "detail": failure.detail}), Fail(failure)),
    )


def test_an_incompatible_engine_is_never_started_again_until_a_reset() -> None:
    failure = EngineFailure("incompatible", "the engine speaks protocol 2; this sidecar speaks 1")
    state = Incompatible(failure)
    for now in (T, T + POLICY.window * 10):
        assert transition(POLICY, state, Request(now)) == Step(state, (Fail(failure),))
    assert transition(POLICY, state, Reset()) == Step(Idle(), ())


@pytest.mark.parametrize(
    "state", [Crashed((T - 1.0,), "crashed"), Unavailable((T - 3.0, T - 2.0, T - 1.0), "crashed"), Idle()]
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


def test_closing_a_running_engine_shuts_it_down_and_is_logged() -> None:
    assert transition(POLICY, Ready((), PID, INFO), Close()) == Step(Closed(), (Shutdown(), Log(ENGINE_CLOSED, {})))


@pytest.mark.parametrize(
    "state",
    [
        Idle(),
        Crashed((T,), "crashed"),
        Unavailable((T, T, T), "crashed"),
        Incompatible(EngineFailure("incompatible", "x")),
    ],
)
def test_closing_without_an_engine_is_logged_and_kills_nothing(state: SupervisionState) -> None:
    assert transition(POLICY, state, Close()) == Step(Closed(), (Log(ENGINE_CLOSED, {}),))


def test_closed_is_final() -> None:
    step = transition(POLICY, Closed(), Request(T))
    assert step == Step(Closed(), (Fail(EngineFailure("closed", "the engine client was closed")),))
    assert transition(POLICY, Closed(), Reset()) == Step(Closed(), ())
    assert transition(POLICY, Closed(), Close()) == Step(Closed(), ())  # closing twice logs once


# -- Random walks (EP-17) -----------------------------------------------------------------------------------------

RUNNING = (Handshaking, Ready, Busy)
FOLLOW_UPS = (Spawn, Handshake, Pause)


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
        check_step(self, before, step, rule.targets, rule.effects)
        self.state = step.state
        self.pending = None
        for effect in step.effects:
            if isinstance(effect, (Kill, Shutdown)):
                self.alive = False
            elif isinstance(effect, FOLLOW_UPS):
                self.pending = effect
        check_world(self)
        return step


def stop_delay(policy: RestartPolicy, n: int) -> float:
    return policy.delays[min(n, len(policy.delays)) - 1] if policy.delays and n else 0.0


def check_step(
    world: World, before: SupervisionState, step: Step, targets: tuple[type, ...], effects: tuple[type, ...]
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
    if any(isinstance(e, Spawn) for e in step.effects):
        assert isinstance(step.state, Starting)
        assert not isinstance(before, (Incompatible, Closed))
        stops = step.state.stops
        assert len(stops) < world.policy.max_crashes, f"a spawn with the bound reached: {step}"
        if stops:
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
    """Properties (S0040 brief): no spawn in Incompatible or Closed or with the bound reached; every spawn after a
    stop waits out its backoff; at most `max_crashes` stops kept; no step both fails the caller and starts work;
    an engine process exists exactly in Handshaking, Ready and Busy; Unavailable holds the bound's worth of stops;
    Closed is final; every state and effect is one its table row declares."""
    world = World(policy)
    for _ in range(data.draw(st.integers(1, 60))):
        world.now += data.draw(st.sampled_from([0.0, 0.1, 1.0, 5.0, 100.0, 301.0]))
        state, pending = world.state, world.pending
        if isinstance(pending, Spawn):
            outcome = data.draw(st.sampled_from(["spawned", "failed", "interrupted", "interrupted_after"]))
            if outcome in ("spawned", "interrupted_after"):
                world.alive = True
            if outcome == "spawned":
                world.feed(Spawned(PID))
            elif outcome == "failed":
                world.feed(SpawnFailed("no interpreter", world.now))
            else:
                world.feed(Interrupted("interrupted", world.now))
        elif isinstance(pending, Handshake):
            world.feed(
                data.draw(
                    st.sampled_from(
                        [
                            HandshakeOk(INFO),
                            HandshakeOk(OTHER_PROTOCOL),
                            HandshakeForeign("protocol 2"),
                            HandshakeFailed("handshake: timeout", world.now),
                            Interrupted("interrupted", world.now),
                        ]
                    )
                )
            )
        elif isinstance(pending, Pause):
            if data.draw(st.booleans()):
                world.now += pending.seconds
                world.feed(BackoffElapsed())
            else:
                world.feed(Interrupted("interrupted", world.now))
        elif isinstance(state, Busy):
            world.feed(
                data.draw(
                    st.sampled_from(
                        [
                            Answered(),
                            TimedOut("t", world.now),
                            Ended("e", world.now),
                            Violated("v", world.now),
                            Interrupted("i", world.now),
                        ]
                    )
                )
            )
        else:  # a resting state: the public calls (and, while running, the engine dying on its own)
            choices: list[SupervisionEvent] = [Request(world.now), Reset(), Close()]
            if isinstance(state, Ready):
                choices += [Send(), Exited("exit 9", world.now)]
            event = data.draw(st.sampled_from(choices))
            if isinstance(event, Exited):
                world.alive = False  # it died by itself; the table's Kill reaps it
            world.feed(event)
