import imaplib
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_config
from app.schemas.result_schemas import ReloadResponse, TestConnectionResponse
from app.services.fetch_service import stream_fetch
from app.state import get_registry

router = APIRouter()


class TestConnectionRequest(BaseModel):
    profile_id: str


class FetchRequest(BaseModel):
    profile_id: str
    since_date: Optional[str] = None   # ISO format: "2026-03-01"
    until_date: Optional[str] = None   # ISO format: "2026-03-15"


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(req: TestConnectionRequest):
    """
    Connectivity test — connect, login, select folder, count matching emails.
    Loads IMAP credentials from the account profile YAML identified by profile_id.
    On success, performs a lightweight IMAP SEARCH to count matching emails.
    """
    from datetime import date, timedelta
    from app.core.imap_client import _derive_domains

    registry = get_registry()
    config = get_config()
    parser = registry.get_parser_by_profile_id(req.profile_id)
    if parser is None:
        return {"success": False, "error": f"Unknown profile: {req.profile_id}"}
    srv = parser.rule.imap_server
    profile = parser.rule
    lookback_days = profile.lookback_days or config.default_lookback_days
    since_date = date.today() - timedelta(days=lookback_days)
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
        return TestConnectionResponse(
            success=True,
            email_count=total,
            message=f"Connected. Found {total} matching emails in the last {lookback_days} days.",
        )
    except Exception as e:
        return TestConnectionResponse(success=False, error=str(e))


@router.post("/reload", response_model=ReloadResponse)
async def reload_profiles():
    """Re-scan rules directory and reload all account profiles."""
    registry = get_registry()
    registry.reload()
    profiles = registry.list_profiles()
    return ReloadResponse(profiles_loaded=len(profiles), message=f"Reloaded {len(profiles)} account profiles.")


@router.post("/fetch")
async def fetch_transactions(req: FetchRequest):
    """
    Stream fetch progress as Server-Sent Events (text/event-stream).

    Loads the account profile by profile_id, reads IMAP credentials from the
    profile's imap_server block, and applies date range/lookback precedence.

    Each event is a JSON ProgressEvent (see result_schemas.py).
    Phases emitted: connecting → fetching → parsing → complete.
    Errors are reported as phase='error' events.

    Frontend must use fetch() + ReadableStream (not EventSource) because
    this is a POST endpoint with a JSON body.
    """
    from datetime import date as date_type

    config = get_config()
    registry = get_registry()

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
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )
