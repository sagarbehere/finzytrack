"""
Tests for app.logging_context — per-request user_id correlation substrate.

Contract:
1. When no user_id is set, the filter writes the placeholder "-" onto the
   LogRecord — log lines from startup / shutdown / background work still
   render cleanly.
2. When set_user_id(value) is called, subsequent records get value.
3. asyncio scopes contextvars per task — two concurrent tasks see their own
   user_id, never each other's.
"""

import asyncio
import logging

from app.logging_context import (
    UserIdLogFilter,
    set_user_id,
    get_user_id,
)


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_filter_writes_placeholder_when_no_user():
    set_user_id(None)
    record = _make_record()
    assert UserIdLogFilter().filter(record) is True
    assert record.user_id == "-"


def test_filter_writes_set_user_id():
    set_user_id("sagar")
    record = _make_record()
    UserIdLogFilter().filter(record)
    assert record.user_id == "sagar"


def test_concurrent_tasks_see_their_own_user_id():
    """asyncio scopes contextvars per task — verify no cross-task leakage."""
    captured: dict[str, str] = {}

    async def worker(uid: str) -> None:
        set_user_id(uid)
        # Yield to the event loop so both workers are interleaved
        await asyncio.sleep(0)
        captured[uid] = get_user_id() or "-"

    async def runner() -> None:
        await asyncio.gather(worker("alice"), worker("bob"))

    asyncio.run(runner())
    assert captured == {"alice": "alice", "bob": "bob"}


def test_get_user_id_returns_set_value():
    set_user_id("zoe")
    assert get_user_id() == "zoe"
    set_user_id(None)
    assert get_user_id() is None
