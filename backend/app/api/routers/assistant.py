"""
AI Assistant chat endpoint.

POST /api/assistant/chat — accepts a conversation + optional file attachment,
runs an agentic tool-use loop against the configured LLM, and streams the
response back as server-sent events (SSE).

SSE event format (one JSON object per event, terminated by \\n\\n):
  {"type": "token",       "content": "..."}        — text chunk
  {"type": "tool_start",  "tool": "...", "message": "..."}  — tool starting
  {"type": "tool_result", "tool": "...", "success": bool, "message": "..."}
  {"type": "error",       "message": "..."}
  {"type": "done"}
"""

import base64
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.client import (
    DoneEvent,
    ToolCallEvent,
    TokenEvent,
    build_assistant_message,
    build_tool_result_message,
    stream_chat,
)
from app.ai.file_processor import process_file
from app.ai.system_prompt import build_system_prompt
from app.ai.tool_registry import ToolRegistry
from app.ai.tools.list_accounts import ListAccountsTool
from app.ai.tools.list_rule_files import ListRuleFilesTool
from app.ai.tools.read_file import ReadFileTool
from app.ai.tools.write_csv_rule import WriteCsvRuleTool
from app.ai.tools.write_email_rule import WriteEmailRuleTool
from app.ai.tools.write_xls_rule import WriteXlsRuleTool
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.email_import.rule_registry import AccountProfileRegistry
from app.dependencies import (
    get_beancount_manager,
    get_config_manager,
    get_csv_rules_manager,
    get_email_registry,
    get_xls_rules_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_AGENT_ITERATIONS = 5

_CSV_EXTENSIONS  = {".csv", ".tsv", ".txt"}
_XLS_EXTENSIONS  = {".xls", ".xlsx", ".xlsm", ".xlsb"}
_EML_EXTENSIONS  = {".eml"}


def _detect_file_type(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext in _CSV_EXTENSIONS:
        return "csv"
    if ext in _XLS_EXTENSIONS:
        return "xls"
    if ext in _EML_EXTENSIONS:
        return "email"
    return None

# Human-readable messages shown while a tool is running
_TOOL_MESSAGES = {
    "write_csv_rule": "Saving CSV rule...",
    "write_xls_rule": "Saving XLS rule...",
    "write_email_rule": "Saving email rule...",
    "read_file": "Reading file...",
    "list_accounts": "Looking up accounts...",
    "list_rule_files": "Listing rule files...",
}


# ── Request / Response models ─────────────────────────────────────────────────

class AttachedFile(BaseModel):
    name: str
    content_base64: str


class ChatMessage(BaseModel):
    role: str
    content: str


class AssistantChatRequest(BaseModel):
    messages: list[ChatMessage]
    file: AttachedFile | None = None
    context: dict | None = None


# ── Helper: build tool registry ───────────────────────────────────────────────

def _build_registry(
    csv_rules_manager: CsvRulesManager,
    xls_rules_manager: XlsRulesManager,
    email_registry: AccountProfileRegistry,
    beancount_manager: BeancountManager,
) -> ToolRegistry:
    csv_dir = Path(csv_rules_manager.rules_dir) if csv_rules_manager.rules_dir else None
    xls_dir = Path(xls_rules_manager.rules_dir) if xls_rules_manager.rules_dir else None
    email_dir = email_registry.rules_directory

    allowed_read_dirs = [d for d in [csv_dir, xls_dir, email_dir] if d is not None]

    registry = ToolRegistry()
    registry.register(WriteCsvRuleTool(csv_dir))
    registry.register(WriteXlsRuleTool(xls_dir))
    registry.register(WriteEmailRuleTool(email_dir))
    registry.register(ReadFileTool(allowed_read_dirs))
    registry.register(ListAccountsTool(beancount_manager))
    registry.register(ListRuleFilesTool(csv_dir, xls_dir, email_dir))
    return registry


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent_loop(
    llm_config,
    messages: list[dict],
    registry: ToolRegistry,
) -> AsyncIterator[dict]:
    """
    Run the agentic tool-use loop and yield SSE-ready event dicts.

    `messages` should already include the system message as the first element.
    """
    provider = llm_config.provider
    tool_schemas = registry.get_schemas(provider)

    for iteration in range(MAX_AGENT_ITERATIONS):
        accumulated_text = ""
        tool_calls_this_turn: list[ToolCallEvent] = []

        async for event in stream_chat(llm_config, messages, tool_schemas):
            if isinstance(event, TokenEvent):
                accumulated_text += event.content
                yield {"type": "token", "content": event.content}
            elif isinstance(event, ToolCallEvent):
                tool_calls_this_turn.append(event)
            elif isinstance(event, DoneEvent):
                break

        if not tool_calls_this_turn:
            # No tools requested — we're done
            if not accumulated_text.strip():
                # Model returned nothing — surface it rather than silently showing a blank response
                yield {"type": "error", "message": "The model returned an empty response. Please try again."}
            yield {"type": "done"}
            return

        # Add the assistant turn (text + tool calls) to history
        messages.append(build_assistant_message(accumulated_text, tool_calls_this_turn))

        # Execute each tool call and add results
        for tc in tool_calls_this_turn:
            yield {
                "type": "tool_start",
                "tool": tc.name,
                "message": _TOOL_MESSAGES.get(tc.name, f"Running {tc.name}..."),
            }

            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}

            result = await registry.execute(tc.name, args)

            success = result.get("success", True)
            # Build a friendly message for the UI
            if success:
                if "path" in result:
                    ui_message = f"Saved to `{result['path']}`"
                elif "accounts" in result:
                    ui_message = f"Found {len(result['accounts'])} accounts"
                elif "files" in result:
                    ui_message = f"Found {len(result['files'])} files"
                elif "content" in result:
                    ui_message = "File read successfully"
                else:
                    ui_message = "Done"
            else:
                ui_message = result.get("error", "Unknown error")

            yield {
                "type": "tool_result",
                "tool": tc.name,
                "success": success,
                "message": ui_message,
            }

            messages.append(build_tool_result_message(tc.id, result))

    # Fell through — hit iteration limit
    yield {"type": "error", "message": "Reached maximum tool-call iterations. Please try again."}
    yield {"type": "done"}


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/assistant/chat")
async def assistant_chat(
    body: AssistantChatRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    csv_rules_manager: CsvRulesManager = Depends(get_csv_rules_manager),
    xls_rules_manager: XlsRulesManager = Depends(get_xls_rules_manager),
    email_registry: AccountProfileRegistry = Depends(get_email_registry),
):
    config = config_manager.get_config()
    llm_config = config.ai.llm

    if not llm_config.model:
        async def _not_configured():
            yield f"data: {json.dumps({'type': 'error', 'message': 'LLM is not configured. Please set ai.llm.model in your config.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(_not_configured(), media_type="text/event-stream")

    # Process attached file
    file_annotation = ""
    file_warning = None
    file_type: str | None = None
    if body.file:
        file_type = _detect_file_type(body.file.name)
        try:
            file_bytes = base64.b64decode(body.file.content_base64)
            file_text, file_warning = process_file(file_bytes, body.file.name)
            file_annotation = f"\n\n--- Attached file: {body.file.name} ---\n{file_text}"
        except Exception as e:
            logger.warning(f"Failed to process attached file: {e}")
            file_annotation = f"\n\n[File attachment failed to process: {e}]"

    # Build context, injecting detected file type so the prompt loader can
    # include only the relevant schema (keeps the prompt small for local models)
    context = {**(body.context or {})}
    if file_type:
        context["file_type"] = file_type

    registry = _build_registry(csv_rules_manager, xls_rules_manager, email_registry, beancount_manager)

    async def generate():
        # Load prompt inside the generator so any missing-file error surfaces
        # as a clean SSE error event rather than a 500 HTTP response.
        try:
            system_prompt = build_system_prompt(context)
        except FileNotFoundError as e:
            logger.error(f"Assistant prompt file missing: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Server configuration error: {e}'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for i, msg in enumerate(body.messages):
            content = msg.content
            if i == len(body.messages) - 1 and msg.role == "user" and file_annotation:
                content = content + file_annotation
            messages.append({"role": msg.role, "content": content})

        # If the file was truncated, prepend a note as the first token
        if file_warning:
            yield f"data: {json.dumps({'type': 'token', 'content': f'*Note: {file_warning}*' + chr(10) + chr(10)})}\n\n"

        try:
            async for event in _run_agent_loop(llm_config, messages, registry):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"Agent loop error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
