"""
Tests for LLMConfig.extra_request_body — the Settings → AI → Advanced power-user
escape hatch that lets users pass provider-specific request parameters into the
underlying SDK call.
"""

from app.ai.client import (
    _PROTECTED_REQUEST_KEYS,
    _filter_protected_keys,
    _resolve_extra_request_body,
)
from app.config import LLMConfig


class TestFilterProtectedKeys:
    def test_passes_through_unprotected_keys(self):
        out = _filter_protected_keys({"top_p": 0.9, "frequency_penalty": 0.5})
        assert out == {"top_p": 0.9, "frequency_penalty": 0.5}

    def test_strips_known_protected_keys(self):
        extras = {
            "model": "evil",
            "messages": [],
            "stream": False,
            "tools": [],
            "tool_choice": "none",
            "system": "evil",
            "temperature": 9.9,
            "max_tokens": 1,
            "top_p": 0.9,
        }
        out = _filter_protected_keys(extras)
        assert out == {"top_p": 0.9}

    def test_protected_set_includes_temperature_and_max_tokens(self):
        # First-class fields must not be overrideable via extras.
        assert "temperature" in _PROTECTED_REQUEST_KEYS
        assert "max_tokens" in _PROTECTED_REQUEST_KEYS

    def test_empty_input_returns_empty(self):
        assert _filter_protected_keys({}) == {}


class TestResolveExtraRequestBody:
    def _config(self, **overrides) -> LLMConfig:
        defaults = dict(model="m", api_url="http://x", provider="openai")
        defaults.update(overrides)
        return LLMConfig(**defaults)

    def test_none_returns_empty_dict(self):
        cfg = self._config(extra_request_body=None)
        assert _resolve_extra_request_body(cfg) == {}

    def test_empty_dict_returns_empty_dict(self):
        cfg = self._config(extra_request_body={})
        assert _resolve_extra_request_body(cfg) == {}

    def test_filters_protected_keys(self):
        cfg = self._config(
            extra_request_body={
                "chat_template_kwargs": {"enable_thinking": False},
                "model": "evil",
                "temperature": 9.9,
            }
        )
        assert _resolve_extra_request_body(cfg) == {
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def test_ignored_when_finzytrack_ai_enabled(self):
        cfg = self._config(
            finzytrack_ai=True,
            extra_request_body={"top_p": 0.9},
        )
        assert _resolve_extra_request_body(cfg) == {}


class TestLLMConfigSchema:
    def test_field_defaults_to_none(self):
        cfg = LLMConfig()
        assert cfg.extra_request_body is None

    def test_field_accepts_dict(self):
        cfg = LLMConfig(extra_request_body={"top_p": 0.9})
        assert cfg.extra_request_body == {"top_p": 0.9}

    def test_field_round_trips_through_model_dump(self):
        cfg = LLMConfig(extra_request_body={"a": {"b": 1}})
        dumped = cfg.model_dump()
        restored = LLMConfig(**{k: v for k, v in dumped.items() if k != "is_configured"})
        assert restored.extra_request_body == {"a": {"b": 1}}
