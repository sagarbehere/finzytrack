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
from app.ai.validation import GroundTruth, ResponseValidator
from app.ai.tools.list_accounts import ListAccountsTool
from app.ai.tools.list_rule_files import ListRuleFilesTool
from app.ai.tools.match_email_against_rules import MatchEmailAgainstRulesTool
from app.ai.tools.read_file import ReadFileTool
from app.ai.tools.test_email_extraction import TestEmailExtractionTool
from app.ai.tools.write_csv_rule import WriteCsvRuleTool
from app.ai.tools.write_email_rule import WriteEmailRuleTool
from app.ai.tools.execute_query import ExecuteQueryTool
from app.ai.tools.get_ledger_context import GetLedgerContextTool
from app.ai.tools.write_xls_rule import WriteXlsRuleTool
from app.core.backup_manager import BackupManager
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.email_import.rule_registry import AccountProfileRegistry
from app.dependencies import (
    get_backup_manager,
    get_beancount_manager,
    get_config_manager,
    get_csv_rules_manager,
    get_email_registry,
    get_xls_rules_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_AGENT_ITERATIONS = 8

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
    "match_email_against_rules": "Checking existing email rules...",
    "test_email_extraction": "Testing extraction patterns...",
    "execute_query": "Running query...",
    "get_ledger_context": "Loading ledger context...",
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
    backup_manager: BackupManager,
    file_type: str | None = None,
    sqlite_path: str | None = None,
) -> ToolRegistry:
    csv_dir = Path(csv_rules_manager.rules_dir) if csv_rules_manager.rules_dir else None
    xls_dir = Path(xls_rules_manager.rules_dir) if xls_rules_manager.rules_dir else None
    email_dir = email_registry.rules_directory

    allowed_read_dirs = [d for d in [csv_dir, xls_dir, email_dir] if d is not None]

    registry = ToolRegistry()

    if file_type:
        # Setup mode — register only tools relevant to the attached file type
        registry.register(ReadFileTool(allowed_read_dirs))
        registry.register(ListAccountsTool(beancount_manager))
        registry.register(ListRuleFilesTool(csv_dir, xls_dir, email_dir))

        if file_type == "csv":
            registry.register(WriteCsvRuleTool(csv_dir, backup_manager))
        elif file_type == "xls":
            registry.register(WriteXlsRuleTool(xls_dir, backup_manager))
        elif file_type == "email":
            registry.register(WriteEmailRuleTool(email_dir, backup_manager))
            registry.register(MatchEmailAgainstRulesTool(email_registry))
            registry.register(TestEmailExtractionTool())
    else:
        # No file attached — analyst/recipe mode
        # get_ledger_context already returns accounts with balances, so
        # list_accounts is not registered here to avoid redundant tool calls.
        if sqlite_path:
            registry.register(ExecuteQueryTool(sqlite_path))
            registry.register(GetLedgerContextTool(beancount_manager, sqlite_path))

    return registry


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent_loop(
    llm_config,
    messages: list[dict],
    registry: ToolRegistry,
    *,
    analyst_mode: bool = False,
) -> AsyncIterator[dict]:
    """
    Run the agentic tool-use loop and yield SSE-ready event dicts.

    `messages` should already include the system message as the first element.

    When *analyst_mode* is True, ground truth is collected from tool results and
    the final response is validated against it. Validation warnings are emitted
    as `validation_warning` SSE events before the `done` event.
    """
    provider = llm_config.provider
    tool_schemas = registry.get_schemas(provider)
    ground_truth = GroundTruth()
    validator = ResponseValidator()

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
            # Normal exit — model finished with text, no more tool calls
            if not accumulated_text.strip():
                yield {"type": "error", "message": "The model returned an empty response. Please try again."}
                yield {"type": "done"}
                return

            # ── Response validation (analyst mode only) ───────────────────
            if analyst_mode:
                results = validator.validate(accumulated_text, ground_truth)
                for r in results:
                    event: dict = {
                        "type": "validation_warning",
                        "rule": r.rule_name,
                        "severity": r.status,
                        "message": r.message,
                    }
                    if r.details:
                        event["details"] = r.details
                    yield event

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

            # ── Collect ground truth from tool results ────────────────────
            if analyst_mode:
                ground_truth.tools_called.append(tc.name)
                if tc.name == "get_ledger_context" and result.get("success"):
                    for acct in result.get("accounts", []):
                        ground_truth.known_accounts.add(acct["account"])
                        if acct.get("currency"):
                            ground_truth.known_currencies.add(acct["currency"])
                    dr = result.get("date_range", {})
                    ground_truth.date_range = (dr.get("min_date"), dr.get("max_date"))
                    ground_truth.default_currency = result.get("default_currency")
                elif tc.name == "execute_query" and result.get("success"):
                    ground_truth.last_query_columns = result.get("columns", [])
                    ground_truth.last_query_rows = result.get("rows", [])

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
                elif "date_range" in result:
                    dr = result["date_range"]
                    ui_message = f"Ledger context loaded ({dr.get('min_date', '?')} to {dr.get('max_date', '?')})"
                elif "row_count" in result:
                    truncated = " (truncated)" if result.get("truncated") else ""
                    ui_message = f"Query returned {result['row_count']} rows{truncated}"
                elif "outcome" in result:
                    # match_email_against_rules
                    outcome = result["outcome"]
                    if outcome == "full_match":
                        ui_message = f"Email already handled by {result.get('matched_type', 'existing rule')}"
                    elif outcome == "sender_match_no_type":
                        ui_message = "Sender recognised, new email format"
                    else:
                        ui_message = "No existing rule matches"
                elif "summary" in result and "all_fields_matched" in result.get("summary", {}):
                    # test_email_extraction
                    summary = result["summary"]
                    if summary["all_fields_matched"]:
                        ui_message = "All extraction patterns matched"
                    else:
                        failed = summary.get("failed_fields", [])
                        ui_message = f"Extraction failed for: {', '.join(failed)}"
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
    backup_manager: BackupManager = Depends(get_backup_manager),
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
    # include only the relevant schema (keeps the prompt small for local models).
    # On follow-up turns the file may not be re-sent, but the frontend persists
    # the session mode and file_type so we can still route to the correct tools.
    context = {**(body.context or {})}
    if file_type:
        context["file_type"] = file_type
    elif context.get("file_type"):
        # Follow-up turn — frontend sent the file_type from the original attachment
        file_type = context["file_type"]

    sqlite_path = config.analytics.sqlite.export_path
    registry = _build_registry(
        csv_rules_manager, xls_rules_manager, email_registry,
        beancount_manager, backup_manager,
        file_type=file_type, sqlite_path=sqlite_path,
    )

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

        # In analyst mode the model must call tools — never answer from memory.
        analyst_mode = not file_type and sqlite_path
        try:
            async for event in _run_agent_loop(
                llm_config, messages, registry,
                analyst_mode=bool(analyst_mode),
            ):
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
