"""
LLM provider factory — supports OpenAI-compatible endpoints and Anthropic.

All providers yield a common stream of events:
  TokenEvent   — a text chunk to stream to the user
  ToolCallEvent — the LLM wants to call a tool
  DoneEvent    — the response is complete
"""
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

from app.config import LLMConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_MAX_TOKENS_DEFAULT = 8192
_anthropic_max_tokens_warned = False


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
class TokenEvent:
    content: str


@dataclass
class ToolCallEvent:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class DoneEvent:
    pass


ChatEvent = TokenEvent | ToolCallEvent | DoneEvent


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
    temp = temperature if temperature is not None else config.temperature

    if config.provider == "anthropic":
        return await _complete_anthropic(config, messages, temp)
    return await _complete_openai(config, messages, temp)


async def stream_chat(
    config: LLMConfig,
    messages: list[dict],
    tools: list[dict],  # provider-specific schemas (caller passes correct format)
) -> AsyncIterator[ChatEvent]:
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
    from openai import AsyncOpenAI

    kwargs = {"api_key": config.api_key or "not-needed"}
    if config.api_url:
        # Ensure the base URL ends without a trailing /v1 (SDK appends it)
        base = config.api_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        kwargs["base_url"] = base

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

    stream = await client.chat.completions.create(**call_kwargs)
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if delta.content:
            yield TokenEvent(content=delta.content)

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

    # Emit completed tool calls
    if tool_call_accum:
        logger.debug("Tool calls received: %s", [acc["name"] for acc in tool_call_accum.values()])
    else:
        logger.debug("No tool calls in this response (text-only)")
    for acc in tool_call_accum.values():
        yield ToolCallEvent(id=acc["id"], name=acc["name"], arguments=acc["arguments"])

    yield DoneEvent()


# ── Anthropic streaming ───────────────────────────────────────────────────────

async def _stream_anthropic(
    config: LLMConfig,
    messages: list[dict],
    tools: list[dict],
) -> AsyncIterator[ChatEvent]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config.api_key or "not-needed")

    # Anthropic takes system separately; extract it from messages
    system_content = ""
    anthropic_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_content = msg["content"] if isinstance(msg["content"], str) else ""
        else:
            anthropic_messages.append(_to_anthropic_message(msg))

    call_kwargs = dict(
        model=config.model,
        max_tokens=_resolve_anthropic_max_tokens(config.max_tokens),
        messages=anthropic_messages,
        temperature=config.temperature,
        stream=True,
    )
    if system_content:
        call_kwargs["system"] = system_content
    if tools:
        call_kwargs["tools"] = tools

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

    kwargs: dict = {"api_key": config.api_key or "not-needed"}
    if config.api_url:
        base = config.api_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        kwargs["base_url"] = base

    client = AsyncOpenAI(**kwargs)
    call_kwargs: dict = dict(
        model=config.model,
        messages=messages,
        temperature=temperature,
        stream=False,
    )
    if config.max_tokens > 0:
        call_kwargs["max_tokens"] = config.max_tokens

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

    client = anthropic.AsyncAnthropic(api_key=config.api_key or "not-needed")

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
        call_kwargs["system"] = system_content

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


def build_tool_result_message(tool_call_id: str, result: dict) -> dict:
    """Build an OpenAI-format tool result message."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result),
    }


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
                    # Normalize empty arguments to valid JSON object —
                    # some providers reject "" and require "{}"
                    "arguments": tc.arguments if tc.arguments else "{}",
                },
            }
            for tc in tool_calls
        ]
    return msg
