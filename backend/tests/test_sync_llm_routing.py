"""Spec: the sync LLM call sites (``ai_categorizer``, ``llm_extractor``)
route through the canonical ``ai.client.complete_chat`` — so they inherit
the proxy resolution, prompt caching, ``extra_request_body`` filtering,
and timeout policy of the streaming assistant.

Lock in the contract that both modules go through ``complete_chat_sync``
instead of hand-rolling provider-specific clients.
"""
import asyncio
import threading
from unittest.mock import patch

import pytest

from app.ai.client import complete_chat_sync
from app.config import LLMConfig
from pydantic import SecretStr


def _llm_config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        api_url="https://test.example.com",
        api_key=SecretStr("test-key"),
        model="gpt-4o",
        temperature=0.0,
        max_tokens=512,
        timeout_secs=30,
        max_tool_rounds=4,
    )


class TestCompleteChatSync:
    def test_routes_through_complete_chat_when_no_loop_running(self):
        captured: dict = {}

        async def fake_complete_chat(config, messages, temperature=None):
            captured["messages"] = messages
            captured["temperature"] = temperature
            return "ok"

        with patch("app.ai.client.complete_chat", fake_complete_chat):
            result = complete_chat_sync(
                _llm_config(),
                [{"role": "user", "content": "hi"}],
            )
        assert result == "ok"
        assert captured["messages"] == [{"role": "user", "content": "hi"}]

    def test_safe_when_called_inside_a_running_event_loop(self):
        """The categorize-transactions route is ``async def``, so when it
        calls into a sync helper that needs to run an async coroutine, we
        must not blow up with ``asyncio.run() cannot be called from a
        running event loop``. ``complete_chat_sync`` detects the running
        loop and offloads to a worker thread.
        """
        async def fake_complete_chat(config, messages, temperature=None):
            return "from-loop"

        async def runner():
            with patch("app.ai.client.complete_chat", fake_complete_chat):
                return complete_chat_sync(
                    _llm_config(),
                    [{"role": "user", "content": "hi"}],
                )

        result = asyncio.run(runner())
        assert result == "from-loop"

    def test_no_loop_path_runs_in_calling_thread(self):
        """Sanity: when no loop is running, ``complete_chat_sync`` uses
        ``asyncio.run`` in the calling thread — no worker thread overhead.
        """
        invoked_in: list[str] = []
        main_thread_name = threading.current_thread().name

        async def fake_complete_chat(config, messages, temperature=None):
            invoked_in.append(threading.current_thread().name)
            return ""

        with patch("app.ai.client.complete_chat", fake_complete_chat):
            complete_chat_sync(
                _llm_config(),
                [{"role": "user", "content": "hi"}],
            )

        assert invoked_in == [main_thread_name]


class TestAICategorizerRoutesThroughCanonicalClient:
    def test_categorize_batch_calls_complete_chat_sync(self):
        from app.services.ai_categorizer import _call_llm

        with patch(
            "app.services.ai_categorizer.complete_chat_sync",
            return_value='[{"id": "txn_0", "account": "Expenses:Food"}]',
        ) as m:
            content = _call_llm(_llm_config(), "user message")
        assert m.called
        # First positional arg is the LLMConfig; second is the messages list
        # which must include the categoriser's system prompt.
        args, _kwargs = m.call_args
        sent_messages = args[1]
        assert sent_messages[-1] == {"role": "user", "content": "user message"}
        assert sent_messages[0]["role"] == "system"
        assert "category" in sent_messages[0]["content"].lower()
        assert content.startswith("[")


class TestLLMExtractorRoutesThroughCanonicalClient:
    def test_extract_fields_llm_calls_complete_chat_sync(self):
        from app.email_import.llm_extractor import extract_fields_llm

        with patch(
            "app.email_import.llm_extractor.complete_chat_sync",
            return_value='{"amount": "10.00", "payee": "Cafe", "date": "2024-01-15", "reference": null, "masked_account": null, "is_debit": true}',
        ) as m:
            result = extract_fields_llm(
                body_text="You spent ₹10",
                subject="Card swipe",
                llm_config=_llm_config(),
            )
        assert m.called
        args, _ = m.call_args
        sent_messages = args[1]
        # The system prompt from email_extractor.md should be the first
        # message; the user message contains the email subject/body.
        assert sent_messages[0]["role"] == "system"
        assert sent_messages[-1]["role"] == "user"
        assert "Card swipe" in sent_messages[-1]["content"]
        # The function returns a dict with parsed fields.
        assert result["payee"] == "Cafe"
