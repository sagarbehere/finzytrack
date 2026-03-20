"""IMAP connection and email fetching."""
import email
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional

from app.core.html_extractor import html_to_text

logger = logging.getLogger(__name__)


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class RawEmail:
    """Full email with stripped body text."""
    message_id: str
    from_address: str
    subject: str
    date: datetime
    body_text: str       # HTML-stripped plain text


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _decode_header_value(raw: str) -> str:
    """Decode RFC 2047 encoded-word headers like =?UTF-8?B?...?="""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(part)
    return ' '.join(decoded)


def _extract_bare_email(from_header: str) -> str:
    """Extract bare email from 'Display Name <email@domain.com>'."""
    m = re.search(r'<([^>]+)>', from_header)
    return m.group(1) if m else from_header


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text from email. HTML-first — bank emails are HTML-only."""
    text_plain = None
    text_html = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            charset = part.get_content_charset() or 'utf-8'
            if ct == 'text/plain' and text_plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    text_plain = payload.decode(charset, errors='replace')
            elif ct == 'text/html' and text_html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    text_html = payload.decode(charset, errors='replace')
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or 'utf-8'
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(charset, errors='replace')
            if ct == 'text/html':
                text_html = body
            else:
                text_plain = body

    # HTML-first: bank emails are typically HTML-only
    if text_html:
        return html_to_text(text_html)
    if text_plain:
        return text_plain
    return ''


# ─── Public function ───────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    """Result of fetch_emails including truncation info."""
    emails: List[RawEmail]
    total_matched: int           # total UIDs before truncation
    truncated: bool              # True if total_matched > max_emails


def _derive_domains(bank_emails: List[str]) -> List[str]:
    """Extract unique domains from bank email addresses."""
    domains = set()
    for addr in bank_emails:
        parts = addr.strip().split('@')
        if len(parts) == 2:
            domains.add(parts[1].lower())
    return sorted(domains)


def fetch_emails(
    server: str,
    port: int,
    username: str,
    password: str,
    folder: str,
    since_date: date,
    until_date: Optional[date] = None,
    bank_emails: Optional[List[str]] = None,
    max_emails: int = 500,
    timeout_secs: int = 30,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> FetchResult:
    """
    Connect to IMAP, search for emails in the date range, fetch their full bodies,
    and return a FetchResult.

    FROM domains are auto-derived from bank_emails. If bank_emails span multiple
    domains, one IMAP SEARCH is run per unique domain and results are merged and
    deduplicated by Message-ID.

    If total matching UIDs exceed max_emails, only the most recent max_emails are
    fetched and truncated=True is set in the result.

    Credentials (server, port, username, password, folder) come from the account
    profile YAML and are passed explicitly by the caller (fetch_service.py).
    Uses readonly=True on select to avoid marking messages as read.
    """
    since_str = since_date.strftime('%d-%b-%Y')  # e.g. "01-Mar-2026"
    from_domains = _derive_domains(bank_emails or [])

    results: List[RawEmail] = []

    with imaplib.IMAP4_SSL(server, port) as imap:
        if timeout_secs > 0:
            imap.socket().settimeout(timeout_secs)
        imap.login(username, password)
        imap.select(folder, readonly=True)

        # Build date criteria
        date_criteria = f'SINCE {since_str}'
        if until_date:
            # IMAP BEFORE is exclusive, so add 1 day
            before_str = (until_date + timedelta(days=1)).strftime('%d-%b-%Y')
            date_criteria = f'SINCE {since_str} BEFORE {before_str}'

        # Run one IMAP SEARCH per unique domain, merge UIDs
        all_uids: List[bytes] = []
        if from_domains:
            for domain in from_domains:
                criteria = f'FROM "{domain}" {date_criteria}'
                status, data = imap.search(None, criteria)
                if status == 'OK' and data[0]:
                    all_uids.extend(data[0].split())
        else:
            # No bank_emails configured — search without FROM filter
            status, data = imap.search(None, date_criteria)
            if status == 'OK' and data[0]:
                all_uids = data[0].split()

        # Deduplicate UIDs (in case domains overlap)
        all_uids = list(dict.fromkeys(all_uids))  # preserves order, removes dupes
        total_matched = len(all_uids)

        # Apply max_emails limit (keep most recent = last N UIDs)
        truncated = total_matched > max_emails
        if truncated:
            all_uids = all_uids[-max_emails:]
            logger.warning(f"Truncating: showing {max_emails} of {total_matched} emails")

        total = len(all_uids)
        logger.info(f"IMAP search: {total_matched} total, fetching {total} (since={since_str})")

        # Fetch full RFC822 body for each UID
        for i, uid in enumerate(all_uids, start=1):
            status, fetch_data = imap.fetch(uid, '(RFC822)')
            if status != 'OK' or not fetch_data or not fetch_data[0]:
                if progress_callback:
                    progress_callback(i, total)
                continue

            raw_bytes = fetch_data[0][1]
            msg = email.message_from_bytes(raw_bytes)

            message_id = msg.get('Message-ID', '').strip() or f'no-id-{uid.decode()}'
            from_raw = _decode_header_value(msg.get('From', ''))
            from_address = _extract_bare_email(from_raw)
            subject = _decode_header_value(msg.get('Subject', ''))
            date_raw = msg.get('Date', '')
            try:
                email_date = parsedate_to_datetime(date_raw)
            except Exception:
                email_date = datetime.now(tz=timezone.utc)

            body_text = _extract_body(msg)

            results.append(RawEmail(
                message_id=message_id,
                from_address=from_address,
                subject=subject,
                date=email_date,
                body_text=body_text,
            ))

            if progress_callback:
                progress_callback(i, total)

    # Deduplicate by Message-ID (in case multi-domain searches returned overlapping emails)
    seen_ids = set()
    deduped = []
    for e in results:
        if e.message_id not in seen_ids:
            seen_ids.add(e.message_id)
            deduped.append(e)

    return FetchResult(emails=deduped, total_matched=total_matched, truncated=truncated)
