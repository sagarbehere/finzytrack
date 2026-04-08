"""Email import API endpoints.

Provides:
  GET  /api/import/email/profiles        - list configured account profiles
  POST /api/import/email/reload          - re-scan rules directory
  POST /api/import/email/test-connection - validate IMAP credentials
  POST /api/import/email/fetch           - stream fetch progress as SSE
  POST /api/import/email/trial-extract   - run rule against .eml for validation
  GET  /api/import/email/rules/{f}/raw   - read raw YAML of an email rule
  POST /api/import/email/rules           - create a new email rule file
  PUT  /api/import/email/rules/{f}       - update an email rule file
  DELETE /api/import/email/rules/{f}     - delete an email rule file
"""
import email as email_stdlib
import imaplib
import logging
import re
from datetime import date as date_type, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.ai.tools.test_email_extraction import _test_one_field
from app.core.backup_manager import BackupManager
from app.email_import.imap_client import _derive_domains
from app.email_import.rule_registry import AccountProfileRegistry
from app.email_import.rule_schemas import RuleFile
from app.email_import.fetch_service import stream_fetch
from app.email_import.result_schemas import (
    FetchRequest,
    ProfilesListResponse,
    ReloadResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    TrialExtractRequest,
    TrialExtractResult,
    TrialExtractedField,
)
from app.dependencies import get_backup_manager, get_config_manager, get_email_registry
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.helpers.rule_validation import parse_yaml, resolve_rule_path, validate_rule_schema
from app.schemas.rule_write_schemas import (
    RuleContentResponse,
    RuleCreateRequest,
    RuleDeleteResponse,
    RuleWriteRequest,
    RuleWriteResponse,
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/email/profiles", response_model=ApiResponse[ProfilesListResponse])
async def list_email_profiles(
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """
    Return list of configured account profiles.
    Each profile_id is the filename without .yaml extension.
    Credentials are never included in the response.
    """
    return success_json_response(ProfilesListResponse(
        profiles=registry.list_profiles(),
        invalid_profiles=registry.list_invalid_profiles(),
    ))


@router.post("/email/reload", response_model=ApiResponse[ReloadResponse])
async def reload_email_profiles(
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """Re-scan rules directory and reload all account profiles."""
    registry.reload()
    profiles = registry.list_profiles()
    return success_json_response(ReloadResponse(
        profiles_loaded=len(profiles),
        message=f"Reloaded {len(profiles)} account profiles.",
    ))


@router.post("/email/test-connection", response_model=ApiResponse[TestConnectionResponse])
async def test_email_connection(
    req: TestConnectionRequest,
    registry: AccountProfileRegistry = Depends(get_email_registry),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """
    Connectivity test — connect, login, select folder, count matching emails.
    Loads IMAP credentials from the account profile YAML identified by profile_id.
    """
    config = config_manager.get_config()
    parser = registry.get_parser_by_profile_id(req.profile_id)
    if parser is None:
        raise APIError(f"Unknown profile: {req.profile_id}", "EMAIL_PROFILE_NOT_FOUND", 404)

    cred_error = parser.check_credentials()
    if cred_error:
        raise APIError(cred_error, "EMAIL_CREDENTIALS_ERROR", 400)

    srv = parser.rule.imap_server
    profile = parser.rule
    lookback_days = profile.lookback_days or config.email_import.default_lookback_days
    since_date = date_type.today() - timedelta(days=lookback_days)
    since_str = since_date.strftime('%d-%b-%Y')
    from_domains = _derive_domains(profile.bank_emails)

    try:
        with imaplib.IMAP4_SSL(srv.server, srv.port) as imap:
            imap.login(srv.username, srv.password)
            imap.select(srv.folder, readonly=True)
            # Count matching emails (no body fetch)
            total = 0
            if from_domains:
                seen_uids = set()
                for domain in from_domains:
                    status, data = imap.search(None, f'FROM "{domain}" SINCE {since_str}')
                    if status == 'OK' and data[0]:
                        for uid in data[0].split():
                            seen_uids.add(uid)
                total = len(seen_uids)
            else:
                status, data = imap.search(None, f'SINCE {since_str}')
                if status == 'OK' and data[0]:
                    total = len(data[0].split())
        return success_json_response(TestConnectionResponse(
            success=True,
            email_count=total,
            message=f"Connected. Found {total} matching emails in the last {lookback_days} days.",
        ))
    except imaplib.IMAP4.error as e:
        raise APIError(f"IMAP error: {e}", "EMAIL_IMAP_ERROR", 400)
    except OSError as e:
        raise APIError(f"Connection failed: {e}", "EMAIL_CONNECTION_ERROR", 400)


@router.post("/email/fetch")
async def fetch_email_transactions(
    req: FetchRequest,
    request: Request,
    registry: AccountProfileRegistry = Depends(get_email_registry),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """
    Stream fetch progress as Server-Sent Events (text/event-stream).

    This endpoint uses SSE for real-time progress reporting.
    Errors during streaming are sent as SSE error events.
    """
    config = config_manager.get_config()

    since_date = date_type.fromisoformat(req.since_date) if req.since_date else None
    until_date = date_type.fromisoformat(req.until_date) if req.until_date else None

    async def event_generator():
        async for event in stream_fetch(
            profile_id=req.profile_id,
            config=config,
            registry=registry,
            since_date=since_date,
            until_date=until_date,
            parsing_mode=req.parsing_mode,
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _parse_eml(raw: str) -> tuple[str, str, str]:
    """Extract (from, subject, body) from raw .eml text. Body prefers plain text over HTML."""
    msg = email_stdlib.message_from_string(raw)
    sender = msg.get("from", "")
    subject = msg.get("subject", "")

    body = ""
    is_html = False
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        is_html = True
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            is_html = msg.get_content_type() == "text/html"

    if is_html:
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

    return sender, subject, body


@router.post(
    "/email/trial-extract",
    response_model=ApiResponse[TrialExtractResult],
    operation_id="trialExtractEmail",
)
async def trial_extract_email(
    req: TrialExtractRequest,
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """
    Run a saved email rule against raw .eml content and return extracted fields.

    Pure validation — no IMAP, no imports. Parses the .eml, finds the first
    matching transaction type in the rule, runs all extraction patterns, and
    returns the results so the frontend can display them to the user.
    """
    # Load the rule file via registry (reload first to pick up just-saved files)
    registry.reload()
    profile_id = req.filename.removesuffix(".yaml").removesuffix(".yml")
    parser = registry.get_parser_by_profile_id(profile_id)
    if parser is None:
        return success_json_response(TrialExtractResult(
            success=False,
            error=f"Rule file not found or invalid: {req.filename}",
            note=f"⚠ Could not load rule {req.filename} for validation.",
        ))

    rule: RuleFile = parser.rule

    # Parse the .eml
    try:
        _sender, eml_subject, eml_body = _parse_eml(req.eml_content)
    except Exception as e:
        return success_json_response(TrialExtractResult(
            success=False,
            error=f"Failed to parse .eml: {e}",
            note="⚠ Could not parse the email file.",
        ))

    # Find the first transaction type whose filters match
    matched_type = None
    for txn_type in rule.transaction_types:
        ef = txn_type.email_filter
        if ef.subject_regex and not re.search(ef.subject_regex, eml_subject, re.IGNORECASE):
            continue
        if ef.body_regex and not re.search(ef.body_regex, eml_body, re.IGNORECASE):
            continue
        matched_type = txn_type
        break

    if matched_type is None:
        return success_json_response(TrialExtractResult(
            success=False,
            note=f"⚠ No transaction type in {req.filename} matched this email's subject/body filters.",
        ))

    # Run extraction for each field
    fields: list[TrialExtractedField] = []
    mapping = matched_type.mapping
    has_required_failure = False

    for field_name, field_def in matched_type.extraction.items():
        result = _test_one_field(field_name, field_def, eml_body, eml_subject, None)
        matched = result.get("matched", False)
        # Derive a human label from the mapping (e.g. "amount" → "Amount")
        mapped_to = mapping.get(field_name, field_name)
        label = mapped_to.replace("_", " ").title()

        fields.append(TrialExtractedField(
            field=field_name,
            label=label,
            value=result.get("value"),
            matched=matched,
            error=result.get("error"),
            optional=field_def.optional,
        ))

        if not matched and not field_def.optional:
            has_required_failure = True

    n_ok = sum(1 for f in fields if f.matched)
    n_total = len(fields)

    if has_required_failure:
        note = f"⚠ Rule validated against email: {n_ok}/{n_total} fields extracted. Required fields failed — rule needs fixing."
    else:
        note = f"✓ Rule validated against email: {n_ok}/{n_total} fields extracted successfully."

    return success_json_response(TrialExtractResult(
        success=not has_required_failure,
        transaction_type=matched_type.name,
        fields=fields,
        note=note,
    ))


# ---------------------------------------------------------------------------
# Email rule file CRUD
# ---------------------------------------------------------------------------

def _email_rules_dir(config_manager: ConfigManager) -> Path:
    return Path(config_manager.get_config().email_rules_dir)


@router.get(
    "/email/rules/{filename}/raw",
    response_model=ApiResponse[RuleContentResponse],
    operation_id="getEmailRuleRaw",
)
async def get_email_rule_raw(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Read raw YAML content of an email rule file."""
    rules_dir = _email_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)
    if not target.exists():
        raise APIError(
            message=f"Email rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )
    content = target.read_text(encoding="utf-8")
    return success_json_response(RuleContentResponse(filename=target.name, content=content))


@router.post(
    "/email/rules",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="createEmailRule",
    status_code=201,
)
async def create_email_rule(
    body: RuleCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """Create a new email rule file."""
    rules_dir = _email_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, body.filename)

    if target.exists():
        raise APIError(
            message=f"File '{target.name}' already exists. Use PUT to update it.",
            code=ec.RESOURCE_CONFLICT,
            status_code=409,
            details={"filename": target.name},
        )

    data = parse_yaml(body.content)
    validate_rule_schema(data, RuleFile, "Email")

    rules_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    logger.info(f"Created email rule: {target}")
    registry.reload()

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=False),
        status_code=201,
    )


@router.put(
    "/email/rules/{filename}",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="updateEmailRule",
)
async def update_email_rule(
    filename: str,
    body: RuleWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """Update an existing email rule file with atomic write + backup."""
    rules_dir = _email_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"Email rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )

    data = parse_yaml(body.content)
    validate_rule_schema(data, RuleFile, "Email")

    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write(body.content)
    logger.info(f"Updated email rule: {target}")
    registry.reload()

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=True)
    )


@router.delete(
    "/email/rules/{filename}",
    response_model=ApiResponse[RuleDeleteResponse],
    operation_id="deleteEmailRule",
)
async def delete_email_rule(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
    registry: AccountProfileRegistry = Depends(get_email_registry),
):
    """Delete an email rule file."""
    rules_dir = _email_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"Email rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )

    target.unlink()
    logger.info(f"Deleted email rule: {target}")
    registry.reload()

    return success_json_response(RuleDeleteResponse(filename=target.name))
