"""A fake of the engine client for the scheduler's tests (`app.platform.engine_jobs.scheduler.JobEngine`; S0058).

It behaves like `app.platform.adapters.engine_client.EngineClient` where the scheduler relies on it, without a
process: `start()` spawns a numbered engine and reports each spawn (`spawned`, before the engine can end); every end
is reported (`ended`) once "drained", at once unless `stuck` holds the reports; `call(spawn=False)` never starts an
engine; past `proceed` only Quit anyway ends the engine (engine-jobs §4; S0057 R1-F1). Its ops:

- `ok`: answers at once with `{"op": "ok"}`;
- `block`: waits until `release()` or until the engine is ended (then `cancelled`);
- `gate`: reports `GateReached`, waits for `proceed()` (then answers `{"wrote": True}`) or for its end;
- `crash`: the engine dies by itself mid-request (`crashed`; its end is reported with no reason of ours);
- `gate_die`: as `gate`, but the engine dies right after `proceed` (a save cut mid-write);
- any op in `scripted` (S0062: recover-all's): its next answer, in order: a result mapping, `CRASH` (the engine
  dies mid-request), `CRASH_TO_BOUND` (it dies and that stop reaches the bound) or `BLOCK` (waits as `block` does,
  released by `release(op)`); every such call's params are kept in `params`.

Each end report's peak memory names its engine (1000, 2000 or 4000 plus the serial), so a test can tell them apart.

Every wait is on a condition (EP-28) and ends when the engine ends, so a test never hangs on the fake itself.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.platform.engine import EndReason, EngineEnd, EngineEvent, EngineFailure, EngineInfo, GateReached
from app.platform.engine_supervision import Closed, Idle, Incompatible, Ready, SupervisionState, Unavailable

INFO = EngineInfo(protocol=2, engine_version="0.4.0.dev0", beancount_version="3.2.0")
CRASH = "crash"
CRASH_TO_BOUND = "crash_to_bound"  # the engine dies and that stop reaches the bound (the client is `Unavailable`)
BLOCK = "block"
DEADLINE = 20.0


@dataclass
class _Engine:
    serial: int
    ended: str | None = None  # our reason, or "died"
    past_commit: bool = False
    gate: str = "none"  # none | pending | proceeded


@dataclass
class FakeEngine:
    spawns_per_start: int = 1  # 2: the bytecode retry (spec §5.1), one start that spawns twice
    start_failure: EngineFailure | None = None
    stuck: bool = False  # hold every end report until `release_ends()`
    hold_ends: bool = False  # hold every end report until the next `release_ends()`, for good (the replay, S0059)
    spawned: Callable[[int], None] | None = None
    before_start: Callable[[], None] | None = None  # runs as `start()` begins, before the client has an engine
    hold_start: bool = False  # `start()` waits for an `end()` that finds no engine, which waits for the job's decision
    ended: Callable[[EngineEnd], None] | None = None
    calls: list[tuple[int, str]] = field(default_factory=lambda: [])
    scripted: dict[str, list[object]] = field(default_factory=lambda: {})
    params: list[tuple[int, str, dict[str, object]]] = field(default_factory=lambda: [])
    ends: list[tuple[int, str]] = field(default_factory=lambda: [])
    starts: int = 0
    resets: int = 0
    proceeded: int = 0
    state_now: SupervisionState = field(default_factory=Idle)
    _serial: int = 0
    _live: _Engine | None = None
    _held: list[EngineEnd] = field(default_factory=lambda: [])
    _released: set[str] = field(default_factory=lambda: set())
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _start_released: threading.Event = field(default_factory=threading.Event)
    _decided: bool = False
    _in_call: int = 0

    # -- the port ----------------------------------------------------------------------------------------------

    @property
    def state(self) -> SupervisionState:
        return self.state_now

    def start(self) -> EngineInfo | EngineFailure:
        if self.before_start is not None:
            self.before_start()
        if self.hold_start:
            assert self._start_released.wait(DEADLINE), "no end() released the held start"
        with self._cond:
            self.starts += 1
            if self.start_failure is not None:
                return self.start_failure
            if self._live is not None and self._live.ended is None:
                return INFO
            for n in range(self.spawns_per_start):
                self._serial += 1
                serial = self._serial
                self._cond.release()
                try:
                    if self.spawned is not None:
                        self.spawned(serial)
                finally:
                    self._cond.acquire()
                if n < self.spawns_per_start - 1:  # the failed first start of the bytecode retry: ended by "stop"
                    self._report(EngineEnd(serial, 1000 + serial, "stop", 1, 1000 + serial))
            self._live = _Engine(self._serial)
            self.state_now = Ready((), 1000 + self._serial, INFO)
            return INFO

    def call(
        self,
        op: str,
        params: Mapping[str, object],
        *,
        on_event: Callable[[EngineEvent], None] | None = None,
        spawn: bool = True,
    ) -> Mapping[str, object] | EngineFailure:
        with self._cond:
            eng = self._live
            if eng is None or eng.ended is not None:
                if spawn:
                    msg = "the scheduler never lets the client start an engine inside a call"
                    raise AssertionError(msg)
                return EngineFailure("crashed", "no engine is running")
            self.calls.append((eng.serial, op))
            self._decided = True
            self._cond.notify_all()
            if op in self.scripted:
                self.params.append((eng.serial, op, dict(params)))
                answer = self.scripted[op].pop(0)
                if answer in (CRASH, CRASH_TO_BOUND):
                    if answer == CRASH_TO_BOUND:
                        now = time.monotonic()  # three stops just now: the restart window does not lift them yet
                        self.state_now = Unavailable((now, now, now), "crashed")
                        self.start_failure = EngineFailure("unavailable", "the engine stopped 3 times in 300 s")
                    self._die(eng)
                    return EngineFailure("crashed", "the engine's output ended")
                if answer == BLOCK:
                    self._in_call += 1
                    self._cond.wait_for(lambda: op in self._released or eng.ended is not None)
                    self._in_call -= 1
                    self._cond.notify_all()
                    if eng.ended is not None:
                        return EngineFailure("cancelled", "ended by us")
                    self._released.discard(op)
                    answer = self.scripted[op].pop(0)
                assert isinstance(answer, dict), answer
                return answer  # pyright: ignore[reportUnknownVariableType]
            if op == "ok":
                return {"op": op}
            if op == "crash":
                self._die(eng)
                return EngineFailure("crashed", "the engine's output ended")
            if op == "block":
                self._in_call += 1
                self._cond.wait_for(lambda: op in self._released or eng.ended is not None)
                self._in_call -= 1
                self._cond.notify_all()
                if eng.ended is not None:
                    return EngineFailure("cancelled", "ended by us")
                self._released.discard(op)
                return {"op": op}
            if op in ("gate", "gate_die"):  # `gate_die`: a save cut mid-write, after `proceed` (S0058 verifier F5)
                eng.gate = "pending"
            else:
                msg = f"the fake has no op {op!r}"
                raise AssertionError(msg)
        if on_event is not None:
            on_event(GateReached())
        with self._cond:
            self._cond.wait_for(lambda: eng.gate == "proceeded" or eng.ended is not None)
            if eng.ended is not None:
                return EngineFailure("cancelled", "ended by us")
            if op == "gate_die":
                self._die(eng)
                return EngineFailure("crashed", "the engine's output ended")
            self._cond.wait_for(lambda: "gate" in self._released or eng.ended is not None)
            self._released.discard("gate")
            eng.past_commit = False
            if eng.ended is not None:
                return EngineFailure("cancelled", "ended by us")
            return {"wrote": True}

    def proceed(self) -> bool:
        with self._cond:
            eng = self._live
            if eng is None or eng.ended is not None or eng.gate != "pending":
                return False
            eng.gate = "proceeded"
            eng.past_commit = True
            self.proceeded += 1
            self._cond.notify_all()
            return True

    def end(self, reason: EndReason) -> None:
        with self._cond:
            self.ends.append((self._live.serial if self._live else 0, reason))
            eng = self._live
            if eng is None or eng.ended is not None:
                if self.hold_start and not self._start_released.is_set():
                    # The end found nothing to end, as the client's does before a start: let the held start return
                    # now, and come back only once the job has acted on its engine (a call, or its own end).
                    self._decided = False
                    self._start_released.set()
                    assert self._cond.wait_for(lambda: self._decided, DEADLINE), "the job never acted"
                return
            if eng.past_commit and reason != "quit_anyway":
                return  # §4: past the commit point only Quit anyway ends the engine
            eng.ended = reason
            self._decided = True
            self.state_now = Idle()
            self._cond.notify_all()
            self._report(EngineEnd(eng.serial, 1000 + eng.serial, reason, -9, 2000 + eng.serial))

    def reset(self) -> None:
        """As the client's: it takes the call lock, so it waits while a request is in flight."""
        with self._cond:
            self._cond.wait_for(lambda: self._in_call == 0)
            self.resets += 1
            self.state_now = Idle()
            self.start_failure = None  # the client's reset forgets the stops and an incompatible verdict

    # -- the test's controls -----------------------------------------------------------------------------------

    def release(self, op: str = "block") -> None:
        """Let the next waiting `block` (or a proceeded `gate`) answer."""
        with self._cond:
            self._released.add(op)
            self._cond.notify_all()

    def die(self) -> int:
        """The running engine dies by itself (between requests or during one); returns its serial."""
        with self._cond:
            eng = self._live
            assert eng is not None
            self._die(eng)
            return eng.serial

    def release_ends(self) -> None:
        with self._cond:
            held, self._held = self._held, []
            self.stuck = False  # `hold_ends` stays

        for end in held:
            if self.ended is not None:
                self.ended(end)

    def unavailable(self, stops: tuple[float, ...]) -> None:
        """The bound is reached: the next start fails `unavailable` (S0040)."""
        with self._cond:
            self.state_now = Unavailable(stops, "crashed")
            self.start_failure = EngineFailure("unavailable", "the engine stopped 3 times in 300 s")

    def incompatible(self) -> None:
        with self._cond:
            failure = EngineFailure("incompatible", "the engine speaks protocol 3")
            self.state_now = Incompatible(failure)
            self.start_failure = failure

    def available(self) -> None:
        with self._cond:
            self.state_now = Idle()
            self.start_failure = None

    def close_client(self) -> None:
        with self._cond:
            self.state_now = Closed()

    @property
    def held(self) -> int:
        """How many end reports are held."""
        with self._cond:
            return len(self._held)

    @property
    def serial(self) -> int:
        return self._serial

    # -- inside ------------------------------------------------------------------------------------------------

    def _die(self, eng: _Engine) -> None:
        eng.ended = "died"
        self._cond.notify_all()
        self._report(EngineEnd(eng.serial, 1000 + eng.serial, None, -11, 4000 + eng.serial))

    def _report(self, end: EngineEnd) -> None:
        """Under the lock: the end is reported on a thread of its own, as the client's reaper does."""
        if self.stuck or self.hold_ends:
            self._held.append(end)
            self._cond.notify_all()
            return
        if self.ended is not None:
            threading.Thread(target=self.ended, args=(end,), name="fake-reaper", daemon=True).start()
