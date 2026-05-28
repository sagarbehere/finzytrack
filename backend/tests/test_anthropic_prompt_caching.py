"""Anthropic prompt-caching tests.

Spec under test: ``_stream_anthropic`` and ``_complete_anthropic`` mark the
system prompt and the tail of the tools list with
``cache_control={"type": "ephemeral"}`` so Anthropic caches them for the
session, cutting input-token cost by ~90% on cache hits across the
multi-round agent loop. The caller's ``tools`` list is not mutated.
"""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.client import (
    _apply_anthropic_system_cache,
    _apply_anthropic_tools_cache,
    _complete_anthropic,
    _stream_anthropic,
)
from app.config import LLMConfig
from pydantic import SecretStr


# ── Pure-function unit tests ─────────────────────────────────────────────────


class TestSystemCacheHelper:
    def test_wraps_string_as_block_with_cache_control(self):
        result = _apply_anthropic_system_cache("You are a helpful assistant.")
        assert result == [
            {
                "type": "text",
                "text": "You are a helpful assistant.",
                "cache_control": {"type": "ephemeral"},
            }
        ]


class TestToolsCacheHelper:
    def test_returns_empty_for_empty_input(self):
        assert _apply_anthropic_tools_cache([]) == []

    def test_marks_only_last_tool(self):
        tools = [
            {"name": "tool_a", "input_schema": {}},
            {"name": "tool_b", "input_schema": {}},
            {"name": "tool_c", "input_schema": {}},
        ]
        result = _apply_anthropic_tools_cache(tools)
        assert "cache_control" not in result[0]
        assert "cache_control" not in result[1]
        assert result[2]["cache_control"] == {"type": "ephemeral"}

    def test_does_not_mutate_caller_list(self):
        """The caller (``ToolRegistry.get_schemas``) returns the canonical
        tool list each turn. Mutating it would leak the cache annotation
        across calls and across calls to providers that don't understand it.
        """
        tools = [
            {"name": "tool_a", "input_schema": {}},
            {"name": "tool_b", "input_schema": {}},
        ]
        _apply_anthropic_tools_cache(tools)
        for t in tools:
            assert "cache_control" not in t, (
                "caller's tool dict must not be modified"
            )

    def test_does_not_mutate_last_tool_in_place(self):
        last = {"name": "tool_b", "input_schema": {}}
        tools = [{"name": "tool_a", "input_schema": {}}, last]
        _apply_anthropic_tools_cache(tools)
        assert "cache_control" not in last


# ── End-to-end: cache hints reach the SDK ────────────────────────────────────


def _llm_config_anthropic() -> LLMConfig:
    return LLMConfig(
        provider="anthropic",
        api_url="",
        api_key=SecretStr("test-key"),
        model="claude-sonnet-4-6",
        temperature=0.0,
        max_tokens=4096,
        timeout_secs=60,
        max_tool_rounds=12,
    )


@pytest.fixture
def captured_call(monkeypatch):
    """Stub ``anthropic.AsyncAnthropic`` so we can assert how the SDK is
    invoked without making real network calls.

    Returns a dict that's populated with the keyword args passed to
    ``messages.stream`` / ``messages.create`` after the function under
    test runs.
    """
    captured: dict[str, Any] = {}

    class _StreamCtx:
        async def __aenter__(self):
            return _IterableStream()

        async def __aexit__(self, *args):
            return None

    class _IterableStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _StreamCtx()

        async def create(self, **kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.content = [MagicMock(text="ok")]
            resp.stop_reason = "end_turn"
            return resp

    class _FakeAnthropicClient:
        def __init__(self, **_):
            self.messages = _Messages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", _FakeAnthropicClient)
    return captured


import asyncio


async def _drain_stream(cfg, messages, tools):
    async for _ in _stream_anthropic(cfg, messages, tools=tools):
        pass


def test_stream_anthropic_marks_system_with_cache_control(captured_call):
    cfg = _llm_config_anthropic()
    messages = [
        {"role": "system", "content": "Stable session prompt."},
        {"role": "user", "content": "Hello"},
    ]
    asyncio.run(_drain_stream(cfg, messages, []))

    system = captured_call["system"]
    assert isinstance(system, list), "system must be a list of blocks to carry cache_control"
    assert system[0]["text"] == "Stable session prompt."
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_stream_anthropic_marks_last_tool_with_cache_control(captured_call):
    cfg = _llm_config_anthropic()
    messages = [{"role": "user", "content": "Use a tool"}]
    tools = [
        {"name": "tool_a", "input_schema": {}},
        {"name": "tool_b", "input_schema": {}},
    ]
    asyncio.run(_drain_stream(cfg, messages, tools))

    sent_tools = captured_call["tools"]
    assert "cache_control" not in sent_tools[0]
    assert sent_tools[-1]["cache_control"] == {"type": "ephemeral"}
    # Caller's list untouched.
    for t in tools:
        assert "cache_control" not in t


def test_complete_anthropic_marks_system_with_cache_control(captured_call):
    cfg = _llm_config_anthropic()
    messages = [
        {"role": "system", "content": "Stable session prompt."},
        {"role": "user", "content": "Hello"},
    ]
    asyncio.run(_complete_anthropic(cfg, messages, temperature=0.0))

    system = captured_call["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_no_tools_no_tools_key_in_call(captured_call):
    """If the caller passes no tools, we must not invent a tools field."""
    cfg = _llm_config_anthropic()
    messages = [{"role": "user", "content": "Hello"}]
    asyncio.run(_drain_stream(cfg, messages, []))
    assert "tools" not in captured_call
