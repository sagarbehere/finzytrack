"""The open job, the sidecar's side (`app.platform.engine_jobs.opening`; `design/specs/engine-jobs.md` §2's `open`,
§4, §5.1, §5.3, §7, §11; ADR-0059; S0088, BL-0152), over the fake engine port with a real scheduler. Written from the
spec's lines:

- §2: `open` is waited-for (the switch, the boot), runs user code, and Cancel stops it;
- §7: "A moved fingerprint discards the result and runs the job again, at most 3 times in a row; then
  `LEDGER_CHANGING` (§11) with Try again"; "A `.open` journal refuses the job with `WRITES_PAUSED {unfinished_save}`";
- §5.1: each try after the first gets a fresh engine (the last ran user code); §5.3: a waited-for job's engine death
  counts as a stop and ends it `ENGINE_STOPPED` with Try again (only the rebuild after a save is sent uncounted);
- ADR-0059: an encrypted file is refused and named, never read;
- INV-23: the log lines carry counts and names of outcomes, never a path.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.platform.clock import FrozenClock
from app.platform.engine import EngineRefusal
from app.platform.engine_jobs.opening import (
    MAX_TRIES,
    EncryptedFile,
    NotLoadable,
    NotOpened,
    Opened,
    OpenTarget,
    open_spec,
    submit_open,
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket
from app.platform.errors import ErrorCode, WritesPausedDetails
from app.platform.observability import Event as LogEvent
from tests.platform.fake_job_engine import CRASH, FakeEngine

DEADLINE = 20.0
REPO = Path(__file__).resolve().parents[3]
W1 = "ws-1"
W2 = "ws-2"
MAIN = "/L/main.bean"
ALD = Path("/app")
LV = "1/sha256:" + "1" * 64
CLOCK = FrozenClock(datetime(2026, 11, 9, 3, 30, tzinfo=UTC))
TZ = "America/Los_Angeles"  # west of UTC: there it is still 8 November
TODAY = "2026-11-08"  # the civil date where the app runs, not UTC's 9 November (INV-07; S0088 round 1, verifier F3)
TARGET = OpenTarget(workspace_key="k2", root=MAIN, lineage="lin2", app_local_data=ALD)
PARAMS: dict[str, object] = {
    "root": MAIN,
    "app_local_data": str(ALD),
    "workspace_key": "k2",
    "lineage": "lin2",
    "floor": 0,
    "today": TODAY,
    "built_at": CLOCK.now().isoformat(),
}
NOTHING: dict[str, object] = {
    "entries": 0,
    "errors": 0,
    "publish": None,
    "number": None,
    "ledger_version": None,
    "removed": 0,
    "remove_failed": 0,
    "refused": None,
    "refusal": None,
    "line": None,
}


def loaded(**kw: object) -> dict[str, object]:
    out: dict[str, object] = {**NOTHING, "outcome": "loaded", "entries": 7, "publish": "published", "number": 1}
    return {**out, "ledger_version": LV, **kw}


def outcome(name: str, **kw: object) -> dict[str, object]:
    return {**NOTHING, "outcome": name, **kw}


class Rig:
    def __init__(self, scripted: dict[str, list[object]]) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.logged: list[tuple[str, dict[str, object]]] = []
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None, log=self._sink)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)
        # a switch from W1 past its close (§9.1 steps 4-6; BL-0154): only the switch's own jobs start (§3)
        self.jobs.workspace_closed()
        self.jobs.exclusive_began()

    def _sink(self, event: LogEvent, /, **fields: object) -> None:
        self.logged.append((event.name, fields))

    def lines(self) -> list[tuple[str, dict[str, object]]]:
        return [(n, f) for n, f in self.logged if n.startswith("open.")]

    def open(self, target: OpenTarget = TARGET) -> object:
        ticket = submit_open(self.jobs, W2, target, clock=CLOCK)
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert out is not None, "the job never ended"
        return out

    def value(self) -> object:
        out = self.open()
        assert isinstance(out, Done), out
        return out.value

    def ops(self) -> list[str]:
        return [op for _, op in self.engine.calls]


@pytest.fixture(autouse=True)
def zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The app's machine in a time zone west of UTC (the engine reads no clock: the sidecar sends the civil date)."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def make() -> Iterator[Any]:
    rigs: list[Rig] = []

    def build(scripted: dict[str, list[object]]) -> Rig:
        rigs.append(Rig(scripted))
        return rigs[-1]

    yield build
    for r in rigs:
        r.jobs.close()


def test_the_open_is_waited_for_and_the_switchs_own_job() -> None:
    spec = open_spec(W2)
    assert (spec.kind, spec.workspace, spec.waited_for, spec.confirmed, spec.owner) == (
        "open",
        W2,
        True,
        False,
        "switch",
    )


def test_the_open_sends_the_root_where_to_publish_and_the_civil_date(make: Any) -> None:
    rig = make({"open": [loaded()]})
    rig.value()
    ((_, op, params),) = rig.engine.params
    assert (op, params) == ("open", PARAMS)


def test_the_request_is_what_the_engine_protocol_takes() -> None:
    schema = json.loads((REPO / "contracts" / "engine-protocol.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$ref": "#/$defs/OpenRequest", "$defs": schema["$defs"]})
    request: Any = {"v": 2, "id": 1, "op": "open", "params": PARAMS}
    assert list(validator.iter_errors(request)) == []  # pyright: ignore[reportUnknownMemberType]
    bad: Any = {**request, "params": {**PARAMS, "fingerprint": "{}"}}  # the open finds the ledger set itself
    assert list(validator.iter_errors(bad)) != []  # pyright: ignore[reportUnknownMemberType]


def test_a_loaded_open_is_opened_with_its_published_mirror_and_logs_counts_only(make: Any) -> None:
    rig = make({"open": [loaded(entries=9, errors=2, removed=1)]})
    value = rig.value()
    assert value == Opened(9, 2, 1, "published", 1, LV, removed=1)
    assert isinstance(value, Opened)
    assert value.mirror_ready
    ((name, fields),) = rig.lines()
    assert name == "open.loaded"
    assert {k: v for k, v in fields.items() if k != "job"} == {
        "workspace_key": "k2",
        "entries": 9,
        "errors": 2,
        "tries": 1,
        "published": "published",
        "number": 1,
        "removed": 1,
        "remove_failed": 0,
    }  # nothing of the ledger (INV-23)


def test_a_fresh_generation_is_opened_without_a_load(make: Any) -> None:
    lv4 = "4/sha256:" + "4" * 64
    rig = make({"open": [outcome("fresh", publish="fresh", number=4, ledger_version=lv4)]})
    value = rig.value()
    assert value == Opened(0, 0, 1, "fresh", 4, lv4)
    assert isinstance(value, Opened)
    assert value.mirror_ready


@pytest.mark.parametrize("why", ["inexact", "incomplete", "replaced"])
def test_a_build_not_published_is_opened_without_a_ready_mirror(make: Any, why: str) -> None:
    """The save's gate refuses `mirror_stale` until a generation is published (publish.md §7): the open says so."""
    rig = make({"open": [loaded(publish=why, number=None, ledger_version=None)]})
    value = rig.value()
    assert isinstance(value, Opened)
    assert value.publish == why
    assert not value.mirror_ready


# -- §7: the tries -----------------------------------------------------------------------------------------------------


def test_a_moved_disk_tries_again_in_a_fresh_engine(make: Any) -> None:
    rig = make({"open": [outcome("moved"), loaded()]})
    assert rig.value() == Opened(7, 0, 2, "published", 1, LV)
    (s1, _, p1), (s2, _, p2) = rig.engine.params
    assert p1 == p2 == PARAMS  # the open finds the ledger set itself each time
    assert s2 != s1  # §5.1: the first try ran user code


def test_a_disk_that_keeps_moving_stops_after_three_tries_with_ledger_changing(make: Any) -> None:
    rig = make({"open": [outcome("moved"), outcome("moved"), outcome("moved"), loaded()]})
    assert MAX_TRIES == 3
    value = rig.value()
    assert isinstance(value, NotOpened)
    assert value.error.code == ErrorCode.LEDGER_CHANGING
    assert rig.ops() == ["open"] * 3
    assert [(n, f["why"]) for n, f in rig.lines()] == [("open.not_loaded", "changing")]


def test_an_unfinished_save_is_writes_paused_and_not_tried_again(make: Any) -> None:
    rig = make({"open": [outcome("paused"), loaded()]})
    value = rig.value()
    assert isinstance(value, NotOpened)
    assert value.error.code == ErrorCode.WRITES_PAUSED
    assert value.error.details == WritesPausedDetails("unfinished_save", None)
    assert rig.ops() == ["open"]
    assert [(n, f["why"]) for n, f in rig.lines()] == [("open.not_loaded", "paused")]


def test_an_encrypted_file_is_refused_and_named_but_never_logged(make: Any) -> None:
    rig = make({"open": [outcome("refused", refused="/L/secret.bean.gpg", refusal="encrypted")]})
    assert rig.value() == EncryptedFile("/L/secret.bean.gpg")
    assert rig.ops() == ["open"]
    ((name, fields),) = rig.lines()
    assert (name, fields["why"]) == ("open.not_loaded", "encrypted")
    assert "/L/secret.bean.gpg" not in repr(fields)


@pytest.mark.parametrize(
    "bad",
    [
        loaded(publish=None),  # a loaded answer always says what became of it
        loaded(publish="fresh"),  # only a fresh answer is fresh
        outcome("fresh", publish="published", number=1, ledger_version=LV),
        outcome("refused"),  # a refusal names its file
        outcome("refused", refusal="encrypted"),  # and its file
        outcome("refused", refused="/L/x.gpg"),  # and why
        loaded(refused="/L/x.gpg"),  # only a refusal does
        loaded(refusal="number_failed"),  # only a refusal says why
        outcome("refused", refused="/L/x.gpg", refusal="encrypted", line=3),  # an encrypted file has no line
        loaded(line=3),  # only a refusal of the ledger names a line
        loaded(number=None),  # S0088 round 1 (verifier F5): a published generation is named by its number
        loaded(ledger_version=None),  # and by its version
        loaded(number=2),  # whose N is that number (LV is 1/...)
        outcome("fresh", publish="fresh", number=None, ledger_version=None),
        loaded(publish="inexact"),  # a build not published names no generation
        {"outcome": "pass"},
    ],
)
def test_an_answer_that_does_not_hold_together_is_internal(make: Any, bad: dict[str, object]) -> None:
    rig = make({"open": [bad]})
    value = rig.value()
    assert isinstance(value, NotOpened)
    assert value.error.code == ErrorCode.INTERNAL
    assert [(n, f["why"]) for n, f in rig.lines()] == [("open.not_loaded", "violation")]


@pytest.mark.parametrize(("why", "line"), [("number_failed", 4), ("beancount_failed", None)])
def test_a_ledger_beancount_cannot_load_is_named_tried_once_and_never_logged(
    make: Any, why: str, line: int | None
) -> None:
    """S0098 (BL-0123): Beancount's own load raised; bean-check fails the same way, so no try again."""
    rig = make({"open": [outcome("refused", refused="/L/main.bean", refusal=why, line=line)]})
    assert rig.value() == NotLoadable(why, "/L/main.bean", line)  # type: ignore[arg-type]
    assert rig.ops() == ["open"]
    ((name, fields),) = rig.lines()
    assert (name, fields["why"]) == ("open.not_loaded", why)
    assert "/L/main.bean" not in repr(fields)


def test_a_refusal_from_the_engine_is_internal(make: Any) -> None:
    rig = make({"open": [EngineRefusal("engine.internal", "an I/O error")]})
    value = rig.value()
    assert isinstance(value, NotOpened)
    assert value.error.code == ErrorCode.INTERNAL


# -- §5.3: the engine's death -----------------------------------------------------------------------------------------


def test_a_death_of_the_opens_engine_is_engine_stopped_and_counted(make: Any) -> None:
    rig = make({"open": [CRASH]})
    out = rig.open()
    assert isinstance(out, Failed), out
    assert out.error.code == ErrorCode.ENGINE_STOPPED
    assert rig.engine.uncounted == []  # a waited-for read: its engine's death is a stop (§5.3)


def test_every_try_is_sent_counted(make: Any) -> None:
    rig = make({"open": [outcome("moved"), loaded()]})
    rig.value()
    assert rig.engine.uncounted == []


def test_the_open_waits_for_the_switchs_window(make: Any) -> None:
    """§3: a switch's own job starts only in the switch's or the boot's window, never beside the open workspace's
    jobs (INV-31)."""
    rig = make({"open": [loaded()]})
    rig.jobs.exclusive_ended()
    rig.jobs.workspace_opened(W1)
    ticket = submit_open(rig.jobs, W2, TARGET, clock=CLOCK)
    assert isinstance(ticket, JobTicket)
    assert ticket.wait(0.2) is None
    assert rig.ops() == []
    rig.jobs.workspace_closed()
    rig.jobs.exclusive_began()
    assert isinstance(ticket.wait(DEADLINE), Done)


def test_the_floor_is_sent(make: Any) -> None:
    rig = make({"open": [loaded()]})
    rig.open(replace(TARGET, floor=41))
    assert rig.engine.params[0][2]["floor"] == 41
