"""Email import API endpoints.

Provides:
  GET  /api/import/email/profiles       - list configured account profiles
  POST /api/import/email/reload         - re-scan rules directory
  POST /api/import/email/test-connection - validate IMAP credentials
  POST /api/import/email/fetch          - stream fetch progress as SSE
"""
import imaplib
from datetime import date as date_type, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.email_import.imap_client import _derive_domains
from app.email_import.rule_registry import AccountProfileRegistry
from app.email_import.fetch_service import stream_fetch
from app.email_import.result_schemas import (
    FetchRequest,
    ProfilesListResponse,
    ReloadResponse,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.dependencies import get_config_manager, get_email_registry
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

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
