"""
LLM provider factory — supports OpenAI-compatible endpoints and Anthropic.

All providers yield a common stream of events:
  TokenEvent   — a text chunk to stream to the user
  ToolCallEvent — the LLM wants to call a tool
  DoneEvent    — the response is complete
"""
import asyncio
import concurrent.futures
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from pydantic import SecretStr

from app.config import LLMConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_MAX_TOKENS_DEFAULT = 8192
_anthropic_max_tokens_warned = False


# Keys we manage ourselves and refuse to let users override via extra_request_body.
# `temperature` and `max_tokens` are first-class fields in Settings; including them
# here prevents the foot-gun of "I changed temperature in Settings but the extras
# field still wins."
_PROTECTED_REQUEST_KEYS = frozenset({
    "model",
    "messages",
    "stream",
    "tools",
    "tool_choice",
    "system",
    "temperature",
    "max_tokens",
})


def _filter_protected_keys(extras: dict) -> dict:
    """Strip keys we manage ourselves; warn once per call about anything dropped."""
    out: dict = {}
    dropped: list[str] = []
    for k, v in extras.items():
        if k in _PROTECTED_REQUEST_KEYS:
            dropped.append(k)
            continue
        out[k] = v
    if dropped:
        logger.warning(
            "Ignoring protected keys in extra_request_body: %s. "
            "These are managed by the assistant and cannot be overridden.",
            sorted(dropped),
        )
    return out


def _resolve_extra_request_body(config: LLMConfig) -> dict:
    """Return user-supplied request extras filtered of protected keys.

    Returns an empty dict when no extras are set, when finzytrack_ai is enabled
    (managed users should never need provider-specific knobs), or when every key
    was protected.
    """
    if not config.extra_request_body or config.finzytrack_ai:
        return {}
    return _filter_protected_keys(config.extra_request_body)


def _resolve_anthropic_max_tokens(configured: int) -> int:
    """Anthropic requires max_tokens. Warn once and fall back if not set."""
    global _anthropic_max_tokens_warned
    if configured > 0:
        return configured
    if not _anthropic_max_tokens_warned:
        logger.warning(
            "Anthropic requires max_tokens but it is set to 0 (model default). "
            "Using fallback of %d. Set max_tokens in Settings → AI to silence this warning.",
            _ANTHROPIC_MAX_TOKENS_DEFAULT,
        )
        _anthropic_max_tokens_warned = True
    return _ANTHROPIC_MAX_TOKENS_DEFAULT


# ── Event types ──────────────────────────────────────────────────────────────

@dataclass
class ThinkingEvent:
    content: str


@dataclass
class TokenEvent:
    content: str


@dataclass
class ToolCallEvent:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class DoneEvent:
    finish_reason: str | None = None
    reasoning_chars: int = 0


ChatEvent = ThinkingEvent | TokenEvent | ToolCallEvent | DoneEvent


# ── Finzytrack AI config resolution ──────────────────────────────────────────

_proxy_config_cache: dict[str, dict] = {}  # proxy_url -> config dict


async def _fetch_proxy_config(proxy_url: str) -> dict:
    """Fetch client settings from the Finzytrack AI proxy's /v1/config endpoint."""
    if proxy_url in _proxy_config_cache:
        return _proxy_config_cache[proxy_url]

    url = proxy_url.rstrip("/") + "/v1/config"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            _proxy_config_cache[proxy_url] = data
            logger.info(
                "Finzytrack AI proxy config: model=%s temperature=%s max_tokens=%s",
                data.get("model"), data.get("temperature"), data.get("max_tokens"),
            )
            return data
    except Exception as exc:
        logger.warning("Failed to fetch config from Finzytrack AI proxy: %s", exc)
    return {}


async def resolve_config(config: LLMConfig) -> LLMConfig:
    """Return the effective LLM config, applying Finzytrack AI overrides if enabled.

    When ``finzytrack_ai`` is True, all LLM settings are fetched from the proxy.
    If the user also configured their own provider settings, a warning is logged
    and Finzytrack AI takes precedence.
    """
    if not config.finzytrack_ai:
        return config

    # Warn if the user also configured bring-your-own fields
    has_own = bool(config.api_key.get_secret_value()) or bool(config.api_url)
    if has_own:
        logger.warning(
            "Both Finzytrack AI and a custom provider are configured. "
            "Finzytrack AI takes precedence — custom provider/api_url/api_key/model are ignored."
        )

    token = config.finzytrack_ai_token.get_secret_value()
    if not token:
        logger.error("Finzytrack AI is enabled but finzytrack_ai_token is empty")
        return config

    proxy_url = config.finzytrack_ai_url
    proxy_cfg = await _fetch_proxy_config(proxy_url)

    updates: dict = {
        "provider": "openai",
        "api_url": proxy_url,
        "api_key": SecretStr(token),
    }
    if proxy_cfg.get("model"):
        updates["model"] = proxy_cfg["model"]
    if "temperature" in proxy_cfg:
        updates["temperature"] = proxy_cfg["temperature"]
    if "max_tokens" in proxy_cfg:
        updates["max_tokens"] = proxy_cfg["max_tokens"]
    if "max_tool_rounds" in proxy_cfg:
        updates["max_tool_rounds"] = proxy_cfg["max_tool_rounds"]
    if "timeout_secs" in proxy_cfg:
        updates["timeout_secs"] = proxy_cfg["timeout_secs"]

    return config.model_copy(update=updates)


# ── Public entry points ───────────────────────────────────────────────────────

async def complete_chat(
    config: LLMConfig,
    messages: list[dict],
    temperature: float | None = None,
) -> str:
    """
    Non-streaming one-shot LLM completion.  Returns the full response text.

    Use this for simple request/response calls (NL parsing, query generation)
    where streaming and tool-use are unnecessary.
    """
    config = await resolve_config(config)
    temp = temperature if temperature is not None else config.temperature

    if config.provider == "anthropic":
        return await _complete_anthropic(config, messages, temp)
    return await _complete_openai(config, messages, temp)


def complete_chat_sync(
    config: LLMConfig,
    messages: list[dict],
    temperature: float | None = None,
) -> str:
    """Synchronous wrapper for ``complete_chat`` callable from any context.

    Existing sync call sites (``services/ai_categorizer.py``,
    ``email_import/llm_extractor.py``) used to hand-roll their own provider
    routing. Routing through this wrapper gets them ``resolve_config``
    (Finzytrack AI proxy), prompt caching, ``extra_request_body`` filtering,
    and one canonical retry/timeout policy — without each site having to
    care which event loop, if any, is in play.

    Two call contexts to support:
      1. Already on a worker thread (e.g. the IMAP fetch thread) — no
         event loop in this thread; ``asyncio.run`` is the fast path.
      2. On the event-loop thread (a sync function called from inside an
         ``async def`` route) — ``asyncio.run`` would raise; offload to a
         single worker thread that gets its own loop. The calling thread
         blocks on the result, which is the same shape as today's
         sync-SDK call — no async regression, but also no improvement.
         The route handler could await ``complete_chat`` directly in the
         future if it wanted to unblock the loop.
    """
    coro = complete_chat(config, messages, temperature)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def stream_chat(
    config: LLMConfig,
    messages: list[dict],
    tools: list[dict],  # provider-specific schemas (caller passes correct format)
) -> AsyncIterator[ChatEvent]:
    config = await resolve_config(config)
    if config.provider == "anthropic":
        async for event in _stream_anthropic(config, messages, tools):
            yield event
    else:
        async for event in _stream_openai(config, messages, tools):
            yield event


# ── OpenAI-compatible streaming ───────────────────────────────────────────────

async def _stream_openai(
    config: LLMConfig,
    messages: list[dict],
    tools: list[dict],
) -> AsyncIterator[ChatEvent]:
    import httpx
    from openai import AsyncOpenAI

    kwargs = {"api_key": config.api_key.get_secret_value() or "not-needed"}
    if config.api_url:
        # Ensure the base URL ends without a trailing /v1 (SDK appends it)
        base = config.api_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        kwargs["base_url"] = base

    # Idle/read timeout — when streaming, this aborts if no bytes arrive for
    # `timeout_secs` (default 120). Without this, a stuck upstream hangs forever.
    kwargs["timeout"] = httpx.Timeout(
        connect=10.0,
        read=float(config.timeout_secs),
        write=30.0,
        pool=10.0,
    )

    client = AsyncOpenAI(**kwargs)

    call_kwargs = dict(
        model=config.model,
        messages=messages,
        temperature=config.temperature,
        stream=True,
    )
    if config.max_tokens > 0:
        call_kwargs["max_tokens"] = config.max_tokens
    if tools:
        call_kwargs["tools"] = tools
        call_kwargs["tool_choice"] = "auto"

    extras = _resolve_extra_request_body(config)
    if extras:
        call_kwargs["extra_body"] = extras

    # Debug: log the request payload (tools + message count, not full content)
    logger.debug(
        "OpenAI request: model=%s, messages=%d, tools=%s, max_tokens=%s",
        config.model,
        len(messages),
        [t["function"]["name"] for t in tools] if tools else [],
        call_kwargs.get("max_tokens", "(not sent)"),
    )
    if tools:
        logger.debug("Tool schemas: %d tools, %d chars", len(tools), len(json.dumps(tools)))

    # Accumulate streamed tool-call deltas keyed by index
    tool_call_accum: dict[int, dict] = {}
    content_chars = 0
    reasoning_chars = 0
    finish_reason: str | None = None
    # Diagnostic: log first-chunk arrival to detect provider-side buffering.
    import time as _time
    stream_start = _time.monotonic()
    first_chunk_logged = False
    first_reasoning_logged = False
    last_progress_log = stream_start

    stream = await client.chat.completions.create(**call_kwargs)
    async for chunk in stream:
        now = _time.monotonic()
        if not first_chunk_logged:
            logger.debug("OpenAI first chunk after %.2fs", now - stream_start)
            first_chunk_logged = True
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish_reason = choice.finish_reason
        delta = choice.delta

        if delta.content:
            content_chars += len(delta.content)
            yield TokenEvent(content=delta.content)

        # Forward reasoning/thinking from OpenAI-compatible models (e.g. DeepSeek-R1)
        # that expose it via a `reasoning_content` field on the delta. Always
        # surfaced — the UI is what users rely on to see what the model is doing.
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_chars += len(reasoning)
            if not first_reasoning_logged:
                logger.debug("OpenAI first reasoning chunk after %.2fs", now - stream_start)
                first_reasoning_logged = True
            # Periodic progress log to confirm chunks arrive over time.
            if now - last_progress_log >= 5.0:
                logger.debug(
                    "OpenAI streaming progress: %.1fs elapsed, reasoning=%d chars, content=%d chars",
                    now - stream_start, reasoning_chars, content_chars,
                )
                last_progress_log = now
            yield ThinkingEvent(content=reasoning)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_accum:
                    tool_call_accum[idx] = {"id": "", "name": "", "arguments": ""}
                acc = tool_call_accum[idx]
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    acc["arguments"] += tc.function.arguments

    logger.debug(
        "OpenAI stream finished: finish_reason=%s, content=%d chars, reasoning=%d chars, tool_calls=%d",
        finish_reason,
        content_chars,
        reasoning_chars,
        len(tool_call_accum),
    )
    # Emit completed tool calls
    if tool_call_accum:
        logger.debug("Tool calls received: %s", [acc["name"] for acc in tool_call_accum.values()])
    else:
        logger.debug("No tool calls in this response (text-only)")
    for acc in tool_call_accum.values():
        yield ToolCallEvent(id=acc["id"], name=acc["name"], arguments=acc["arguments"])

    yield DoneEvent(finish_reason=finish_reason, reasoning_chars=reasoning_chars)


# ── Anthropic prompt caching helpers ──────────────────────────────────────────
#
# Anthropic's prompt caching: marking stable content with
# `cache_control={"type": "ephemeral"}` caches it for ~5 minutes. Subsequent
# requests with the same cached content pay ~10% of the normal input-token
# cost (cache reads); the first request pays 25% extra to write the cache.
# Breakeven at 2 requests — which is well below our 12-round agent loop.
#
# Two breakpoints: the system prompt (stable per session+context) and the
# tail of the tools list (stable per session). Two breakpoints mean changing
# one part doesn't invalidate the other's cache. Conversation history is NOT
# cached — it grows each turn.
#
# References: https://docs.anthropic.com/claude/docs/prompt-caching


def _apply_anthropic_system_cache(system_content: str) -> list[dict]:
    """Wrap *system_content* as a single text block marked for prompt caching.

    Anthropic accepts ``system`` as either a string or a list of blocks; the
    list form is required to attach ``cache_control``. Below the model's
    minimum cacheable size (1024 tokens for Sonnet, 2048 for Haiku) the
    field is silently no-op'd by the API, so there's no risk in marking
    short prompts.
    """
    return [
        {
            "type": "text",
            "text": system_content,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _apply_anthropic_tools_cache(tools: list[dict]) -> list[dict]:
    """Return a copy of *tools* with ``cache_control`` on the last entry.

    Marking the last tool caches the entire preceding tool list. The list
    is copied (and the last item shallow-copied) so we never mutate the
    caller's data — ``ToolRegistry.get_schemas`` returns the canonical list
    each turn, and we'd otherwise leak the cache annotation across calls.
    """
    if not tools:
        return tools
    result = list(tools)
    result[-1] = {**result[-1], "cache_control": {"type": "ephemeral"}}
    return result


# ── Anthropic streaming ───────────────────────────────────────────────────────

async def _stream_anthropic(
    config: LLMConfig,
    messages: list[dict],
    tools: list[dict],
) -> AsyncIterator[ChatEvent]:
    import anthropic

    client_kwargs: dict = {"api_key": config.api_key.get_secret_value() or "not-needed"}
    if config.api_url:
        client_kwargs["base_url"] = config.api_url.rstrip("/")
    client = anthropic.AsyncAnthropic(**client_kwargs)

    # Anthropic takes system separately; extract it from messages
    system_content = ""
    anthropic_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_content = msg["content"] if isinstance(msg["content"], str) else ""
        else:
            anthropic_messages.append(_to_anthropic_message(msg))

    max_tokens = _resolve_anthropic_max_tokens(config.max_tokens)
    call_kwargs = dict(
        model=config.model,
        max_tokens=max_tokens,
        messages=anthropic_messages,
        temperature=config.temperature,
        stream=True,
    )

    if system_content:
        call_kwargs["system"] = _apply_anthropic_system_cache(system_content)
    if tools:
        call_kwargs["tools"] = _apply_anthropic_tools_cache(tools)

    extras = _resolve_extra_request_body(config)
    if extras:
        call_kwargs.update(extras)

    tool_calls: list[dict] = []
    current_tool: dict | None = None
    current_tool_input_buf: str = ""

    async with client.messages.stream(**call_kwargs) as stream:
        async for event in stream:
            etype = event.type

            if etype == "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    current_tool = {"id": block.id, "name": block.name}
                    current_tool_input_buf = ""

            elif etype == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    yield TokenEvent(content=delta.text)
                elif delta.type == "thinking_delta":
                    yield ThinkingEvent(content=delta.thinking)
                elif delta.type == "input_json_delta" and current_tool is not None:
                    current_tool_input_buf += delta.partial_json

            elif etype == "content_block_stop":
                if current_tool is not None:
                    tool_calls.append({
                        **current_tool,
                        "arguments": current_tool_input_buf,
                    })
                    current_tool = None
                    current_tool_input_buf = ""

    for tc in tool_calls:
        yield ToolCallEvent(id=tc["id"], name=tc["name"], arguments=tc["arguments"])

    yield DoneEvent()


# ── Non-streaming completions ─────────────────────────────────────────────────

async def _complete_openai(
    config: LLMConfig, messages: list[dict], temperature: float,
) -> str:
    from openai import AsyncOpenAI

    kwargs: dict = {"api_key": config.api_key.get_secret_value() or "not-needed"}
    if config.api_url:
        base = config.api_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        kwargs["base_url"] = base

    # Match the streaming sibling so sync callers get bounded behaviour.
    # Without this, a stuck upstream hangs forever.
    kwargs["timeout"] = httpx.Timeout(
        connect=10.0,
        read=float(config.timeout_secs),
        write=30.0,
        pool=10.0,
    )

    client = AsyncOpenAI(**kwargs)
    call_kwargs: dict = dict(
        model=config.model,
        messages=messages,
        temperature=temperature,
        stream=False,
    )
    if config.max_tokens > 0:
        call_kwargs["max_tokens"] = config.max_tokens

    extras = _resolve_extra_request_body(config)
    if extras:
        call_kwargs["extra_body"] = extras

    resp = await client.chat.completions.create(**call_kwargs)
    content = resp.choices[0].message.content or ""
    if not content:
        finish = resp.choices[0].finish_reason
        if finish == "length":
            raise RuntimeError(
                "Model ran out of tokens (reasoning took too long). "
                "Try a shorter input or increase max_tokens."
            )
        raise RuntimeError("AI returned empty response")
    return content


async def _complete_anthropic(
    config: LLMConfig, messages: list[dict], temperature: float,
) -> str:
    import anthropic

    client_kwargs: dict = {"api_key": config.api_key.get_secret_value() or "not-needed"}
    if config.api_url:
        client_kwargs["base_url"] = config.api_url.rstrip("/")
    client = anthropic.AsyncAnthropic(**client_kwargs)

    system_content = ""
    anthropic_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_content = msg["content"] if isinstance(msg["content"], str) else ""
        else:
            anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

    call_kwargs: dict = dict(
        model=config.model,
        max_tokens=_resolve_anthropic_max_tokens(config.max_tokens),
        messages=anthropic_messages,
        temperature=temperature,
    )
    if system_content:
        call_kwargs["system"] = _apply_anthropic_system_cache(system_content)

    resp = await client.messages.create(**call_kwargs)
    content = resp.content[0].text if resp.content else ""
    if not content:
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                "Model ran out of tokens. Try a shorter input or increase max_tokens."
            )
        raise RuntimeError("AI returned empty response")
    return content


# ── Message format conversion ─────────────────────────────────────────────────

def _to_anthropic_message(msg: dict) -> dict:
    """Convert an OpenAI-format message to Anthropic format."""
    role = msg["role"]

    if role == "tool":
        # OpenAI tool result → Anthropic user message with tool_result block
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"]),
                }
            ],
        }

    if role == "assistant":
        content_blocks = []
        if msg.get("content"):
            content_blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            try:
                input_data = json.loads(tc["function"]["arguments"])
            except Exception:
                input_data = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["function"]["name"],
                "input": input_data,
            })
        return {"role": "assistant", "content": content_blocks}

    # user message — convert multimodal content blocks if present
    content = msg["content"]
    if isinstance(content, list):
        anthropic_blocks = []
        for block in content:
            btype = block.get("type", "")
            if btype == "image_url":
                # Convert OpenAI image_url → Anthropic image block
                url = block["image_url"]["url"]
                if url.startswith("data:"):
                    # data:image/png;base64,<data>
                    header, data = url.split(",", 1)
                    media_type = header.split(":")[1].split(";")[0]
                    anthropic_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    })
                else:
                    anthropic_blocks.append({
                        "type": "image",
                        "source": {"type": "url", "url": url},
                    })
            elif btype in ("image", "document"):
                # Already in Anthropic format — pass through
                anthropic_blocks.append(block)
            else:
                # text or other — pass through
                anthropic_blocks.append(block)
        return {"role": role, "content": anthropic_blocks}

    return {"role": role, "content": content}


# Runaway-bug detector: only fires when a tool returns clearly absurd
# payload (a bug, not a user's real data). Realistic tools cap by row count
# (`execute_query` at 500 rows) or bytes (`read_file` at 64 KB); those are
# the appropriate semantic limits. This char-level threshold is *not* a
# budget control — context overflow is a real, recoverable failure mode
# surfaced by the provider, handled by `_run_agent_loop`. We don't
# preemptively truncate realistic-but-large results because:
#   1. Truncating mid-payload changes the semantics of "give me X" tools
#      like read_recipe and read_file (the LLM asked for X; returning a
#      notice is misleading).
#   2. Any specific cap embeds a magic number that real users could trip
#      with no advance warning.
#   3. Context overflow already produces a structured 400 from the LLM
#      provider; `_run_agent_loop.is_context_overflow_error` converts that
#      into an actionable user message naming the cause.
_RUNAWAY_TOOL_RESULT_CHARS = 500_000


def build_tool_result_message(tool_call_id: str, result: dict) -> dict:
    """Build an OpenAI-format tool result message.

    Carries the full ``result`` as serialised JSON unless it exceeds
    ``_RUNAWAY_TOOL_RESULT_CHARS`` — at half a megabyte of JSON, the
    cause is a tool bug, not user data. In that case the content is
    replaced with a structured notice so we don't ship a runaway
    response to the model.
    """
    content = json.dumps(result)
    if len(content) > _RUNAWAY_TOOL_RESULT_CHARS:
        notice = {
            "success": result.get("success", True),
            "_truncated": True,
            "_original_size_chars": len(content),
            "_note": (
                f"Tool returned an unreasonably large payload "
                f"({len(content)} chars > {_RUNAWAY_TOOL_RESULT_CHARS}). "
                "Likely a tool bug. Replacing with this notice to avoid "
                "sending a runaway response to the model."
            ),
            "_keys": sorted(result.keys()),
        }
        content = json.dumps(notice)
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


# Substrings indicating the provider rejected the request because input
# exceeded the model's context window. Matched on the exception string
# rather than caught by SDK exception type so the detection stays
# resilient to SDK version changes across providers.
_CONTEXT_OVERFLOW_HINTS = (
    "prompt is too long",
    "context_length_exceeded",
    "maximum context length",
    "exceeds maximum",
    "context length",
    "too many tokens",
)


def is_context_overflow_error(exc: BaseException) -> bool:
    """True when *exc* looks like a context-window overflow from an LLM
    provider. Used by the assistant agent loop to convert opaque 400s into
    an actionable user-facing message.
    """
    msg = str(exc).lower()
    return any(hint in msg for hint in _CONTEXT_OVERFLOW_HINTS)


def _sanitize_arguments(raw: str) -> str:
    """Ensure tool call arguments are valid JSON.

    Providers reject malformed JSON in replayed assistant messages with
    misleading errors like "Context limit exceeded".  If the LLM produced
    invalid JSON, replace it with an empty object — the error details are
    already conveyed to the LLM via the tool result message.
    """
    if not raw:
        return "{}"
    try:
        json.loads(raw)
        return raw
    except (json.JSONDecodeError, ValueError):
        logger.warning("Sanitized invalid tool-call arguments (%d chars) to '{}'", len(raw))
        return "{}"


def build_assistant_message(text: str, tool_calls: list[ToolCallEvent]) -> dict:
    """Build an OpenAI-format assistant message (may include tool calls)."""
    msg: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _sanitize_arguments(tc.arguments),
                },
            }
            for tc in tool_calls
        ]
    return msg
