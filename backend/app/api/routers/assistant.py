"""
AI Assistant chat endpoint.

POST /api/assistant/chat — accepts a conversation + optional file attachment,
runs an agentic tool-use loop against the configured LLM, and streams the
response back as server-sent events (SSE).

SSE event format (one JSON object per event, terminated by \\n\\n):
  {"type": "thinking",    "content": "..."}        — model reasoning chunk
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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.client import (
    DoneEvent,
    ThinkingEvent,
    ToolCallEvent,
    TokenEvent,
    build_assistant_message,
    build_tool_result_message,
    is_context_overflow_error,
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
from app.ai.tools.list_recipes import ListRecipesTool
from app.ai.tools.read_recipe import ReadRecipeTool
from app.ai.tools.get_recipe_schema import GetRecipeSchemaTool
from app.ai.tools.preview_recipe import PreviewRecipeTool
from app.ai.reference import get_readiness
from app.ai.tools.get_example_widget import GetExampleWidgetTool
from app.ai.tools.read_reference import ReadReferenceTool
from app.ai.tools.write_recipe import WriteRecipeTool
from app.helpers.response_helpers import success_json_response
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
    get_sqlite_reader,
    get_xls_rules_manager,
)
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_TOOL_ROUNDS_DEFAULT = 12  # fallback if not in config


@router.get("/ai/diagnostics")
async def ai_diagnostics():
    """Report whether every file the AI assistant depends on is on disk.

    A standalone health-check the user (or a script) can hit to verify that
    `scripts/sync_ai_reference.py` was run and the bundle is intact. This
    prevents 'silent failure' where the assistant believes it has access to
    reference files / schemas that aren't actually present.
    """
    return success_json_response(get_readiness())


def _log_context_size(messages: list[dict], tool_schemas: list[dict], iteration: int) -> None:
    """Log the approximate token count of the context being sent to the LLM."""
    # Rough estimate: serialize to JSON and count chars / 4
    tools_json = json.dumps(tool_schemas)
    tools_chars = len(tools_json)

    total_chars = tools_chars
    per_message = []
    for i, msg in enumerate(messages):
        msg_json = json.dumps(msg)
        msg_chars = len(msg_json)
        total_chars += msg_chars
        role = msg.get("role", "?")
        # For tool results, show the tool name; for others just role
        if role == "tool":
            label = f"tool({msg.get('tool_call_id', '?')[:8]})"
        else:
            label = role
        per_message.append(f"  msg[{i}] {label}: ~{msg_chars // 4} tokens ({msg_chars} chars)")

    est_tokens = total_chars // 4
    logger.debug(
        "Context for iteration %d: ~%d tokens total (%d chars). "
        "Tools: ~%d tokens. Messages: %d",
        iteration, est_tokens, total_chars, tools_chars // 4, len(messages),
    )
    for line in per_message:
        logger.debug(line)

_CSV_EXTENSIONS  = {".csv", ".tsv", ".txt"}
_XLS_EXTENSIONS  = {".xls", ".xlsx", ".xlsm", ".xlsb"}
_EML_EXTENSIONS  = {".eml"}

# Public docs explaining reasoning-model behavior, surfaced from error events.
_REASONING_DOCS_URL = "https://docs.finzytrack.com/reference/reasoning-models"


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
    "list_recipes": "Listing recipes...",
    "read_recipe": "Reading recipe...",
    "write_recipe": "Saving dashboard recipe...",
    "preview_recipe": "Preparing dashboard preview...",
    "get_recipe_schema": "Loading recipe schema...",
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
    sqlite_reader: SqliteReader,
    backup_manager: BackupManager,
    file_type: str | None = None,
    sqlite_path: str | None = None,
    recipes_dir: Path | None = None,
) -> ToolRegistry:
    csv_dir = Path(csv_rules_manager.rules_dir) if csv_rules_manager.rules_dir else None
    xls_dir = Path(xls_rules_manager.rules_dir) if xls_rules_manager.rules_dir else None
    email_dir = email_registry.rules_directory

    allowed_read_dirs = [d for d in [csv_dir, xls_dir, email_dir] if d is not None]

    registry = ToolRegistry()

    if file_type:
        # Setup mode — register only tools relevant to the attached file type
        registry.register(ReadFileTool(allowed_read_dirs))
        registry.register(ListAccountsTool(sqlite_reader))
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
            registry.register(GetLedgerContextTool(sqlite_reader, sqlite_path))
        if recipes_dir:
            registry.register(GetRecipeSchemaTool())
            registry.register(ListRecipesTool(recipes_dir))
            registry.register(ReadRecipeTool(recipes_dir))
            registry.register(PreviewRecipeTool(sqlite_path))
            registry.register(WriteRecipeTool(recipes_dir, sqlite_path, backup_manager))
            registry.register(ReadReferenceTool())
            registry.register(GetExampleWidgetTool())

    return registry


# ── Agent loop ────────────────────────────────────────────────────────────────

async def _run_agent_loop(
    llm_config,
    messages: list[dict],
    registry: ToolRegistry,
    *,
    analyst_mode: bool = False,
    max_iterations: int = MAX_TOOL_ROUNDS_DEFAULT,
    request: Request | None = None,
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

    async def _client_gone() -> bool:
        # True only when the browser-side TCP connection has been torn down
        # (user clicked New Chat / closed tab / closed the desktop app). In-app
        # navigation keeps the AssistantView mounted (KeepAlive), so this stays
        # False during normal sidebar nav.
        if request is None:
            return False
        try:
            return await request.is_disconnected()
        except Exception:
            return False

    # Track the most recent tool name so the context-overflow error can name
    # the likely culprit. The overflow shows up on the *next* iteration's
    # request, so this is the tool whose result we appended just before the
    # failed call.
    last_tool_name: str | None = None

    for iteration in range(max_iterations):
        if await _client_gone():
            logger.info("Assistant client disconnected before iteration %d; aborting", iteration)
            yield {"type": "aborted", "reason": "client_disconnected"}
            return

        # ── Debug: log context size before each LLM call ──────────────
        _log_context_size(messages, tool_schemas, iteration)

        accumulated_text = ""
        tool_calls_this_turn: list[ToolCallEvent] = []
        finish_reason: str | None = None
        reasoning_chars = 0
        client_disconnected = False

        try:
            async for event in stream_chat(llm_config, messages, tool_schemas):
                if await _client_gone():
                    # Break out of the async iterator so the SDK's stream context
                    # closes the upstream HTTP connection and stops billing.
                    client_disconnected = True
                    break
                if isinstance(event, ThinkingEvent):
                    yield {"type": "thinking", "content": event.content}
                elif isinstance(event, TokenEvent):
                    accumulated_text += event.content
                    yield {"type": "token", "content": event.content}
                elif isinstance(event, ToolCallEvent):
                    tool_calls_this_turn.append(event)
                elif isinstance(event, DoneEvent):
                    finish_reason = event.finish_reason
                    reasoning_chars = event.reasoning_chars
                    break
        except Exception as exc:
            if is_context_overflow_error(exc):
                # The accumulated history (system + tools + N rounds of
                # messages + tool results) exceeds the model's context
                # window. Surface an actionable message instead of the
                # provider's opaque 400.
                logger.info(
                    "Context overflow at iteration %d (last tool: %s): %s",
                    iteration, last_tool_name, exc,
                )
                culprit = (
                    f" The previous tool call was '{last_tool_name}', "
                    "which may have produced a large result."
                    if last_tool_name else ""
                )
                yield {
                    "type": "error",
                    "reason": "context_overflow",
                    "message": (
                        "This conversation has grown too large for the model's "
                        "context window. Try starting a new chat, asking a more "
                        "focused question, or switching to a model with a larger "
                        f"context window.{culprit}"
                    ),
                }
                yield {"type": "done"}
                return
            raise

        if client_disconnected:
            logger.info("Assistant client disconnected mid-stream at iteration %d; aborting", iteration)
            yield {"type": "aborted", "reason": "client_disconnected"}
            return

        if not tool_calls_this_turn:
            # Normal exit — model finished with text, no more tool calls
            if not accumulated_text.strip():
                # Diagnose: reasoning model that exhausted its budget on
                # thinking and never produced visible output.
                error_event: dict = {"type": "error"}
                if finish_reason == "length" and reasoning_chars > 0:
                    error_event["message"] = (
                        "The model used its entire token budget on internal reasoning "
                        f"({reasoning_chars:,} chars) and never produced an answer. "
                        "Try a non-reasoning model or raise max_tokens in Settings → AI."
                    )
                    error_event["docs_url"] = _REASONING_DOCS_URL
                elif finish_reason == "length":
                    error_event["message"] = (
                        "The model hit its max_tokens limit before producing an answer. "
                        "Raise max_tokens in Settings → AI."
                    )
                elif reasoning_chars > 0:
                    error_event["message"] = (
                        f"The model produced only internal reasoning ({reasoning_chars:,} chars) "
                        "and no visible answer. Try a different model."
                    )
                    error_event["docs_url"] = _REASONING_DOCS_URL
                else:
                    error_event["message"] = "The model returned an empty response. Please try again."
                yield error_event
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
            if await _client_gone():
                logger.info(
                    "Assistant client disconnected before tool '%s' at iteration %d; aborting",
                    tc.name, iteration,
                )
                yield {"type": "aborted", "reason": "client_disconnected"}
                return
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError as e:
                args = {}

            yield {
                "type": "tool_start",
                "tool": tc.name,
                "message": _TOOL_MESSAGES.get(tc.name, f"Running {tc.name}..."),
                "args": args,
            }

            if not args and tc.arguments:
                # JSON parse failed above — re-parse to get the error
                try:
                    json.loads(tc.arguments)
                except json.JSONDecodeError as e:
                    result = {
                        "success": False,
                        "error": (
                            f"Your tool call had invalid JSON arguments (parse error: {e}). "
                            "Please try again with valid JSON. Keep the recipe simple — "
                            "use fewer widgets and shorter queries."
                        ),
                    }
                    yield {
                        "type": "tool_result",
                        "tool": tc.name,
                        "success": False,
                        "message": f"Invalid JSON in tool arguments: {e}",
                    }
                    messages.append(build_tool_result_message(tc.id, result))
                    continue

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
            # Build a friendly message for the UI.
            # NOTE: order matters — read tools return both `path` and `content`,
            # so the `content` branch must be checked before the `path` branch
            # or we'd mislabel a read as a save.
            if success:
                if "content" in result:
                    if result.get("path"):
                        ui_message = f"Read `{result['path']}`"
                    else:
                        ui_message = "File read successfully"
                elif "path" in result:
                    ui_message = f"Saved to `{result['path']}`"
                elif "accounts" in result:
                    ui_message = f"Found {len(result['accounts'])} accounts"
                elif "files" in result:
                    ui_message = f"Found {len(result['files'])} files"
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
                elif "schema" in result and tc.name == "get_recipe_schema":
                    ui_message = "Recipe schema loaded"
                elif "recipe" in result and tc.name == "preview_recipe":
                    ui_message = f"Preview ready: {result['recipe'].get('title', 'Dashboard')}"
                elif tc.name == "list_recipes" and "dashboards" in result:
                    n_d = len(result.get("dashboards", []))
                    n_w = len(result.get("widgets", []))
                    ui_message = f"Found {n_d} dashboards, {n_w} widgets"
                elif "relative_path" in result:
                    ui_message = f"Saved dashboard `{result['relative_path']}`"
                elif "summary" in result and "all_fields_matched" in result.get("summary", {}):
                    # test_email_extraction
                    summary = result["summary"]
                    filter_warnings = summary.get("filter_warnings", [])
                    required_failures = summary.get("required_failures", summary.get("failed_fields", []))
                    all_failed = summary.get("failed_fields", [])
                    if filter_warnings:
                        ui_message = f"Email filter did not match: {', '.join(filter_warnings)}"
                    elif summary["all_fields_matched"] and not filter_warnings:
                        ui_message = "All extraction patterns matched"
                    elif required_failures:
                        ui_message = f"Extraction failed for required fields: {', '.join(required_failures)}"
                    else:
                        # Only optional fields failed
                        ui_message = f"Required fields OK; optional fields missed: {', '.join(all_failed)}"
                else:
                    ui_message = "Done"
            else:
                ui_message = result.get("error", "Unknown error")

            # Build the data payload for the frontend details panel.
            # Exclude 'success' (already a top-level field) and very large
            # payloads like recipe schemas to keep SSE events manageable.
            result_data = {k: v for k, v in result.items() if k != "success"}
            if tc.name == "get_recipe_schema":
                # Schema text is huge; the frontend doesn't need it for display
                result_data.pop("schema", None)

            tool_result_event: dict = {
                "type": "tool_result",
                "tool": tc.name,
                "success": success,
                "message": ui_message,
                "data": result_data,
            }
            # Attach recipe JSON for preview_recipe so the frontend can render it
            if tc.name == "preview_recipe" and success and "recipe" in result:
                tool_result_event["recipe"] = result["recipe"]
                tool_result_event["recipe_type"] = result.get("recipe_type", "dashboard")

            yield tool_result_event

            messages.append(build_tool_result_message(tc.id, result))
            last_tool_name = tc.name

    # Fell through — hit iteration limit
    yield {
        "type": "error",
        "message": (
            "I used all available tool calls without reaching a final answer. "
            "This usually means the question requires data that isn't in the database. "
            "Please try a simpler or more specific question."
        ),
    }
    yield {"type": "done"}


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/assistant/chat")
async def assistant_chat(
    body: AssistantChatRequest,
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    backup_manager: BackupManager = Depends(get_backup_manager),
    csv_rules_manager: CsvRulesManager = Depends(get_csv_rules_manager),
    xls_rules_manager: XlsRulesManager = Depends(get_xls_rules_manager),
    email_registry: AccountProfileRegistry = Depends(get_email_registry),
):
    config = config_manager.get_config()
    llm_config = config.ai.llm

    if not llm_config.is_configured:
        async def _not_configured():
            yield f"data: {json.dumps({'type': 'error', 'message': 'AI is not configured. Enable Finzytrack AI or set a model under Settings → AI.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(_not_configured(), media_type="text/event-stream")

    # Process attached file
    file_annotation = ""
    file_warning = None
    file_type: str | None = None
    if body.file:
        file_type = _detect_file_type(body.file.name)
        if file_type is None:
            ext = Path(body.file.name).suffix.lower() or "(none)"
            msg = (
                f"Unsupported file type '{ext}'. "
                "Upload a CSV/TSV/TXT, XLS/XLSX/XLSM/XLSB, or .eml file."
            )
            async def _unsupported(message=msg):
                yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return StreamingResponse(_unsupported(), media_type="text/event-stream")
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

    sqlite_path = config.sqlite_export_path
    recipes_dir = Path(config.recipes_dir) if config.recipes_dir else None
    registry = _build_registry(
        csv_rules_manager, xls_rules_manager, email_registry,
        beancount_manager, sqlite_reader, backup_manager,
        file_type=file_type, sqlite_path=sqlite_path,
        recipes_dir=recipes_dir,
    )

    async def generate():
        # Load prompt inside the generator so any missing-file error surfaces
        # as a clean SSE error event rather than a 500 HTTP response.
        try:
            system_prompt = build_system_prompt(context)
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Assistant prompt build failed: {e}")
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
                max_iterations=llm_config.max_tool_rounds,
                request=request,
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
