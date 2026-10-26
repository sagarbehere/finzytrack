"""The interim event log on the sidecar's stderr (S0038; ARCHITECTURE.md §6A; BL-0088 replaces its sink).

One JSON object per line with §6A's common fields (`ts` in UTC with milliseconds, `level`, `event`, `source`,
`app_version`) and the event's own fields; a sink that fails never takes the sidecar down.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from app.platform.adapters.stderr_log import StderrLog
from app.platform.clock import FrozenClock

AT = datetime(2026, 4, 1, 4, 30, 1, 234567, tzinfo=UTC)


def test_an_event_is_one_json_line_with_the_common_fields() -> None:
    out = io.StringIO()
    log = StderrLog(out, FrozenClock(AT), "0.4.0.dev0")
    log("info", "engine.ready", {"pid": 42, "beancount_version": "3.2.0"})
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "ts": "2026-04-01T04:30:01.234Z",
        "level": "info",
        "event": "engine.ready",
        "source": "backend",
        "app_version": "0.4.0.dev0",
        "pid": 42,
        "beancount_version": "3.2.0",
    }


def test_the_source_can_be_the_engine_and_a_line_never_spans_two() -> None:
    out = io.StringIO()
    StderrLog(out, FrozenClock(AT), "v")("info", "engine.stderr", {"source": "engine", "line": "a\nb"})
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert (event["source"], event["line"]) == ("engine", "a\nb")


def test_the_common_fields_cannot_be_overwritten_by_an_events_own_fields() -> None:
    out = io.StringIO()
    StderrLog(out, FrozenClock(AT), "v")("info", "engine.ready", {"event": "x", "level": "debug", "ts": "y"})
    event = json.loads(out.getvalue())
    assert (event["event"], event["level"], event["ts"]) == ("engine.ready", "info", "2026-04-01T04:30:01.234Z")


def test_a_closed_stream_is_no_crash() -> None:
    out = io.StringIO()
    out.close()
    StderrLog(out, FrozenClock(AT), "v")("error", "engine.start_failed", {"kind": "start_failed"})
