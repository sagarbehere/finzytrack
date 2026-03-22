"""
Orchestrate: load account profile -> IMAP fetch -> rule matching -> SSE events.

Architecture:
  - _run_fetch_thread(): synchronous worker; loads profile by profile_id,
    performs all IMAP + parsing work; puts SSE event strings into an asyncio.Queue.
  - stream_fetch(): async generator; starts the thread and yields queue items
    until the sentinel object is received.

The final SSE event has phase='complete' and carries the full FetchResult as
event.result (a JSON-serialisable dict). All other events are progress updates.
"""
import asyncio
import logging
import threading
from datetime import date, timedelta
from typing import AsyncGenerator, Optional

from app.config import Config
from app.email_import.imap_client import fetch_emails
from app.email_import.rule_registry import AccountProfileRegistry
from app.email_import.regex_extractor import ExtractionError
from app.email_import.llm_extractor import LLMExtractionError
from app.email_import.result_schemas import (
    FetchResult, FetchStats, ParsedTransaction,
    UnmatchedEmail, ExtractionErrorInfo,
    ProgressEvent,
)

logger = logging.getLogger(__name__)

_SENTINEL = object()  # signals end-of-stream in the queue


# --- SSE helpers --------------------------------------------------------------

def _make_event(phase: str, message: str, **kwargs) -> str:
    """Serialise a ProgressEvent as an SSE data line."""
    payload = ProgressEvent(phase=phase, message=message, **kwargs)
    return f"data: {payload.model_dump_json()}\n\n"


# --- Background thread -------------------------------------------------------

def _run_fetch_thread(
    profile_id: str,
    since_date_override: Optional[date],
    until_date_override: Optional[date],
    config: Config,
    registry: AccountProfileRegistry,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Worker thread: load account profile -> IMAP fetch -> rule matching -> field extraction.

    Reads IMAP credentials from the account profile's imap_server block.
    Applies date range: UI dates -> compute since_date from lookback_days -> config.default_lookback_days.

    Puts SSE event strings into `queue`. Sends _SENTINEL when done.
    Thread-safe: uses asyncio.run_coroutine_threadsafe to write to the queue.
    """
    email_config = config.email_import

    def put(event: str) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    try:
        # Load the account profile
        parser = registry.get_parser_by_profile_id(profile_id)
        if parser is None:
            put(_make_event('error', f"Unknown profile: '{profile_id}'"))
            return

        cred_error = parser.check_credentials()
        if cred_error:
            put(_make_event('error', cred_error))
            return

        profile = parser.rule

        # Resolve date range
        if since_date_override:
            since_date = since_date_override
        else:
            lookback_days = profile.lookback_days or email_config.default_lookback_days
            since_date = date.today() - timedelta(days=lookback_days)
        until_date = until_date_override or date.today()

        srv = profile.imap_server
        folder = srv.folder

        put(_make_event('connecting', f'Connecting to {srv.server} / {folder}...'))

        def fetch_progress(current: int, total: int) -> None:
            put(_make_event('fetching',
                f'Fetching email {current}/{total}...',
                total=total,
                current=current))

        try:
            fetch_result = fetch_emails(
                server=srv.server,
                port=srv.port,
                username=srv.username,
                password=srv.password,
                folder=folder,
                since_date=since_date,
                until_date=until_date,
                bank_emails=profile.bank_emails,
                max_emails=email_config.max_emails,
                timeout_secs=email_config.imap_timeout_secs,
                progress_callback=fetch_progress,
            )
        except Exception as e:
            logger.exception(f"IMAP error for profile {profile_id}")
            put(_make_event('error', f'IMAP error: {e}'))
            return

        raw_emails = fetch_result.emails
        truncated = fetch_result.truncated

        if truncated:
            put(_make_event('fetching',
                f'Warning: showing {len(raw_emails)} of {fetch_result.total_matched} emails — narrow the date range to see more.',
                total=fetch_result.total_matched,
                current=len(raw_emails)))

        # -- Parsing phase -------------------------------------------------
        parsed_transactions = []
        unmatched_emails = []
        extraction_errors = []
        total_to_parse = len(raw_emails)

        # LLM config comes from shared ai.llm
        llm_config = config.ai.llm

        for i, raw in enumerate(raw_emails, start=1):
            if not raw.body_text:
                put(_make_event('parsing', f'Parsing email {i}/{total_to_parse}...',
                    current=i, total=total_to_parse))
                continue

            # Match against THIS profile's rules only (not all profiles)
            txn_type = parser.find_matching_type(raw.from_address, raw.subject, raw.body_text)
            if txn_type is None:
                unmatched_emails.append(UnmatchedEmail(
                    from_address=raw.from_address,
                    subject=raw.subject,
                    date=raw.date,
                    reason="No matching rule found for this sender/subject/body combination",
                ))
                put(_make_event('parsing', f'Parsing email {i}/{total_to_parse}...',
                    current=i, total=total_to_parse))
                continue

            try:
                data = parser.parse_email(
                    txn_type=txn_type,
                    body_text=raw.body_text,
                    subject=raw.subject,
                    email_date=raw.date,
                    message_id=raw.message_id,
                    parsing_mode=email_config.parsing_mode,
                    llm_config=llm_config,
                )
                parsed_transactions.append(ParsedTransaction(
                    date=data['date'],
                    amount=data['amount'],
                    payee=data['payee'],
                    external_id=data.get('external_id'),
                    external_id_type=data.get('external_id_type'),
                    masked_account=data.get('masked_account'),
                    source_rule=data['source_rule'],
                    raw_email_from=raw.from_address,
                    raw_email_subject=raw.subject,
                    raw_email_date=raw.date,
                    message_id=raw.message_id,
                ))
            except (ExtractionError, LLMExtractionError) as e:
                extraction_errors.append(ExtractionErrorInfo(
                    from_address=raw.from_address,
                    subject=raw.subject,
                    date=raw.date,
                    rule_matched=f"{parser.profile_id}/{txn_type.name}",
                    reason=str(e),
                ))

            put(_make_event('parsing', f'Parsing email {i}/{total_to_parse}...',
                current=i, total=total_to_parse))

        result = FetchResult(
            stats=FetchStats(
                emails_fetched=len(raw_emails),
                transactions_parsed=len(parsed_transactions),
                unmatched=len(unmatched_emails),
                extraction_errors=len(extraction_errors),
                truncated=truncated,
            ),
            transactions=parsed_transactions,
            unmatched_emails=unmatched_emails,
            extraction_errors=extraction_errors,
        )
        # 'complete' event carries the full result for the frontend
        put(_make_event('complete', 'Done',
                        result=result.model_dump(mode='json')))

    except Exception as e:
        logger.exception("Fatal error in fetch thread")
        put(_make_event('error', f'Fatal error: {e}'))
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(_SENTINEL), loop)


# --- Public async generator ---------------------------------------------------

async def stream_fetch(
    profile_id: str,
    config: Config,
    registry: AccountProfileRegistry,
    since_date: Optional[date] = None,
    until_date: Optional[date] = None,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE strings for POST /fetch.

    Starts _run_fetch_thread in a daemon thread and reads events from an
    asyncio.Queue until the sentinel is received.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=_run_fetch_thread,
        args=(profile_id, since_date, until_date,
              config, registry, queue, loop),
        daemon=True,
    )
    thread.start()

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        yield item
