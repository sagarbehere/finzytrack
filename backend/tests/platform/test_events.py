"""The event registry (`app.platform.observability.events`; ARCHITECTURE.md §6A.3; EP-05, EP-26).

Each event declares its name, level, feature, source and typed fields. The registry's self-check is the schema test
EP-26 names: field kinds are the safe set or `debug_detail`, nothing shadows a common field, and the closed kinds
carry their values. Each refusal sits beside the planted case that must not pass (EP-29).
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.platform.engine import EndCause, FailureKind
from app.platform.engine_jobs.queue import JobKind
from app.platform.observability import COMMON, EVENTS, Event, Field, Kind, registry_problems
from app.platform.observability.events import (
    ENGINE_EXIT,
    ENGINE_START_FAILED,
    ENGINE_STOPPED,
    ENGINE_UNAVAILABLE,
    JOB_INTERNAL_ERROR,
)

GOOD = Event("engine.ready", "info", "platform", (Field("pid", Kind.ID), Field("protocol", Kind.ID)))


def test_the_registry_passes_its_own_check() -> None:
    assert registry_problems(EVENTS) == []


def test_a_good_registry_passes() -> None:
    assert registry_problems((GOOD, Event("engine.closed", "info", "platform", ()))) == []


@pytest.mark.parametrize(
    ("events", "problem"),
    [
        ((GOOD, GOOD), "listed twice"),
        ((Event("Engine.Ready", "info", "platform", ()),), "name"),
        ((Event("engine", "info", "platform", ()),), "name"),
        ((Event("engine.ready now", "info", "platform", ()),), "name"),
        ((Event("engine.ready", "fatal", "platform", ()),), "level"),  # type: ignore[arg-type]
        ((Event("engine.ready", "info", "platform", (), source="db"),), "source"),  # type: ignore[arg-type]
        ((Event("engine.ready", "info", "", ()),), "feature"),
        ((Event("engine.ready", "info", "Plat form", ()),), "feature"),
        ((Event("engine.ready", "info", "platform", (Field("ts", Kind.ID),)),), "common field"),
        ((Event("engine.ready", "info", "platform", (Field("session_id", Kind.ID),)),), "common field"),
        ((Event("engine.ready", "info", "platform", (Field("undeclared_fields", Kind.COUNT),)),), "common field"),
        ((Event("engine.ready", "info", "platform", (Field("pid", Kind.ID), Field("pid", Kind.COUNT))),), "twice"),
        ((Event("engine.ready", "info", "platform", (Field("Pid", Kind.ID),)),), "field name"),
        ((Event("engine.ready", "info", "platform", (Field("kind", Kind.ENUM),)),), "values"),
        ((Event("engine.ready", "info", "platform", (Field("note", Kind.SAFE_TEXT),)),), "values"),
        ((Event("engine.ready", "info", "platform", (Field("pid", Kind.ID, frozenset({"a"})),)),), "values"),
        ((Event("engine.ready", "info", "platform", (Field("pid", "money"),)),), "kind"),  # type: ignore[arg-type]
        ((Event("engine.ready", "info", "platform", (Field("k", Kind.ENUM, frozenset({""})),)),), "values"),
    ],
)
def test_a_bad_registry_is_refused(events: tuple[Event, ...], problem: str) -> None:
    problems = registry_problems(events)
    assert any(problem in p for p in problems), problems


def test_common_fields_are_the_line_schema_of_6a1() -> None:
    assert COMMON == (
        "ts",
        "level",
        "event",
        "session_id",
        "correlation_id",
        "ws",
        "source",
        "feature",
        "app_version",
        "debug",
        "undeclared_fields",
    )


def test_the_failure_kinds_logged_are_exactly_the_engines_failure_kinds() -> None:
    (kind,) = (f for f in ENGINE_START_FAILED.fields if f.name == "kind")
    assert kind.kind is Kind.ENUM
    assert kind.values == frozenset(get_args(FailureKind.__value__))


def test_free_text_fields_of_the_engine_events_are_debug_detail_only() -> None:
    """BL-0088: `engine.stderr`'s line and `engine.start_failed`'s detail are shown only in debug mode."""
    kinds = {(e.name, f.name): f.kind for e in EVENTS for f in e.fields}
    assert kinds[("engine.stderr", "line")] is Kind.DEBUG_DETAIL
    assert kinds[("engine.start_failed", "detail")] is Kind.DEBUG_DETAIL


def test_a_stop_is_logged_with_exactly_the_kinds_a_running_engine_can_stop_with() -> None:
    """S0040 brief, as S0057 amends it (engine-jobs §5.3, §5.4: no time limit, our own ends are no stops): a running
    engine stops by crashing or by breaking the protocol; a failed start is `engine.start_failed`, and the other
    failure kinds never stop a running engine."""
    (kind,) = (f for f in ENGINE_STOPPED.fields if f.name == "kind")
    assert kind.kind is Kind.ENUM
    assert kind.values == frozenset({"crashed", "protocol_violation"})
    assert {f.name: f.kind for f in ENGINE_STOPPED.fields} == {"kind": Kind.ENUM, "detail": Kind.DEBUG_DETAIL}
    assert ENGINE_STOPPED.level == "warning"


def test_unavailable_is_logged_with_the_count_of_stops_only() -> None:
    """S0040 decision: the engine that serves every write is down until the window passes: an error, like a failed
    start; its one field is the number of stops."""
    assert {f.name: f.kind for f in ENGINE_UNAVAILABLE.fields} == {"stops": Kind.COUNT}
    assert ENGINE_UNAVAILABLE.level == "error"


def test_an_engines_end_is_logged_with_content_free_fields_only() -> None:
    """S0058 (BL-0150: "log each engine's end report (exit, peak memory) with its job"): the job's kind and the
    end's cause are closed sets held equal to their types; every other field is a number, safe outside debug mode."""
    fields = {f.name: f for f in ENGINE_EXIT.fields}
    assert {n: f.kind for n, f in fields.items()} == {
        "engine": Kind.ID,
        "job": Kind.ENUM,
        "reason": Kind.ENUM,
        "exit_code": Kind.COUNT,
        "signal": Kind.COUNT,
        "max_rss_kib": Kind.COUNT,
    }
    assert fields["job"].values == frozenset(get_args(JobKind.__value__))
    assert fields["reason"].values == frozenset(get_args(EndCause.__value__)) | {"none"}


def test_a_jobs_internal_error_carries_its_text_only_in_debug_mode() -> None:
    assert {f.name: f.kind for f in JOB_INTERNAL_ERROR.fields} == {
        "job": Kind.ID,
        "exception_type": Kind.ID,
        "exception": Kind.DEBUG_DETAIL,
    }
    assert JOB_INTERNAL_ERROR.level == "error"
