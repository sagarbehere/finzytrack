"""The log file (`app.platform.observability.log`; ARCHITECTURE.md §6A.1; ADR-0039; INV-27).

One JSON object per line at `<app_local_data>/logs/finzytrack.jsonl`, with §6A.1's common fields in a fixed order,
size-based rotation, and a startup check that refuses a log path outside the app-local-data folder or inside the
workspace.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.platform.clock import FrozenClock
from app.platform.observability import (
    EventLog,
    LogContext,
    LogRefusal,
    LogSettings,
    Roots,
    bound_context,
    log_file,
    new_session_id,
    valid_session_id,
)
from app.platform.observability.events import ENGINE_CLOSED, ENGINE_READY

AT = datetime(2026, 4, 1, 4, 30, 1, 234567, tzinfo=UTC)


def make(app: Path, settings: LogSettings | None = None) -> tuple[EventLog, Path]:
    path = log_file(app, None)
    assert isinstance(path, Path), path
    log = EventLog(
        path,
        context=LogContext(session_id="0f3c2a1b-session", app_version="0.4.0.dev0"),
        clock=FrozenClock(AT),
        roots=Roots(app, app.parent / "cache"),
        settings=settings or LogSettings(),
    )
    return log, path


@pytest.fixture
def app(tmp_path: Path) -> Path:
    out = tmp_path / "local"
    out.mkdir()
    return out


def test_the_log_is_one_json_line_per_event_with_the_common_fields_first(app: Path) -> None:
    log, path = make(app)
    log(ENGINE_READY, pid=4242, protocol=1, engine_version="0.4.0.dev0", beancount_version="3.2.0")
    with bound_context(correlation_id="req-1", ws="ws-1"):
        log(ENGINE_CLOSED)
    log.close()
    assert path == app / "logs" / "finzytrack.jsonl"
    ready, closed = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert list(ready.items()) == [
        ("ts", "2026-04-01T04:30:01.234Z"),
        ("level", "info"),
        ("event", "engine.ready"),
        ("session_id", "0f3c2a1b-session"),
        ("source", "backend"),
        ("feature", "platform"),
        ("app_version", "0.4.0.dev0"),
        ("pid", 4242),
        ("protocol", 1),
        ("engine_version", "0.4.0.dev0"),
        ("beancount_version", "3.2.0"),
    ]
    assert list(closed)[:6] == ["ts", "level", "event", "session_id", "correlation_id", "ws"]


def test_the_timestamp_is_utc_with_milliseconds_whatever_the_clocks_zone(app: Path) -> None:
    path = log_file(app, None)
    assert isinstance(path, Path)
    log = EventLog(
        path,
        context=LogContext(session_id="0f3c2a1b-session", app_version="v"),
        clock=FrozenClock(datetime(2026, 3, 31, 21, 30, 1, 5000, tzinfo=timezone(timedelta(hours=-7)))),
        roots=Roots(app, app),
    )
    log(ENGINE_CLOSED)
    log.close()
    assert json.loads(path.read_text())["ts"] == "2026-04-01T04:30:01.005Z"


def test_a_line_never_spans_two(app: Path) -> None:
    log, path = make(app)
    log(ENGINE_READY, engine_version="a\nb")
    log.close()
    assert len(path.read_text().splitlines()) == 1


def test_the_log_rotates_by_size_and_keeps_the_configured_number_of_files(app: Path) -> None:
    log, path = make(app, LogSettings(max_bytes=600, backups=2))
    for _ in range(60):
        log(ENGINE_CLOSED)
    log.close()
    files = sorted(p.name for p in path.parent.iterdir())
    assert files == ["finzytrack.jsonl", "finzytrack.jsonl.1", "finzytrack.jsonl.2"]
    for p in path.parent.iterdir():
        assert p.stat().st_size <= 600
        assert all(json.loads(line)["event"] == "engine.closed" for line in p.read_text().splitlines())


def test_the_rotation_defaults_are_10_mb_times_5() -> None:
    assert LogSettings() == LogSettings(max_bytes=10 * 1024 * 1024, backups=5)


def test_the_log_folder_is_created_inside_the_app_local_data_folder(app: Path) -> None:
    path = log_file(app, None)
    assert path == app / "logs" / "finzytrack.jsonl"
    assert (app / "logs").is_dir()


def test_a_logs_folder_that_leads_outside_the_app_local_data_folder_is_refused(tmp_path: Path, app: Path) -> None:
    outside = tmp_path / "synced"
    outside.mkdir()
    (app / "logs").symlink_to(outside, target_is_directory=True)
    out = log_file(app, None)
    assert isinstance(out, LogRefusal)
    assert "outside" in out.reason
    assert list(outside.iterdir()) == []


def test_a_log_file_that_links_outside_is_refused(tmp_path: Path, app: Path) -> None:
    (app / "logs").mkdir()
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    (app / "logs" / "finzytrack.jsonl").symlink_to(target)
    assert isinstance(log_file(app, None), LogRefusal)


def test_a_log_file_inside_the_workspace_is_refused(tmp_path: Path) -> None:
    """INV-27: the log is never inside a workspace folder (here the app folder was placed in it)."""
    ws = tmp_path / "ws"
    app = ws / "app-data"
    app.mkdir(parents=True)
    out = log_file(app, ws)
    assert isinstance(out, LogRefusal)
    assert "workspace" in out.reason


def test_a_log_folder_that_cannot_be_made_is_refused(app: Path) -> None:
    (app / "logs").write_text("a file, not a folder")
    assert isinstance(log_file(app, None), LogRefusal)


def test_a_sibling_workspace_is_fine(tmp_path: Path, app: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    assert log_file(app, ws) == app / "logs" / "finzytrack.jsonl"


def test_session_ids() -> None:
    first, second = new_session_id(), new_session_id()
    assert first != second
    assert valid_session_id(first)
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    for good in ("0f3c2a1b", "A1B2-C3D4-e5f6", "x" * 64):
        assert valid_session_id(good), good
    for bad in ("", "short", "x" * 65, "has space1", "a:b:c:d:e", "näive-id1", "abc/def/gh", "line\nbreak1"):
        assert not valid_session_id(bad), bad
