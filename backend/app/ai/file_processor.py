"""
File processor for the AI assistant.

Reads uploaded CSV, XLS/XLSX, and EML files and returns a text representation
suitable for inclusion in an LLM prompt.

For large CSV/XLS files the middle transaction rows are omitted, but:
  - All header rows (non-data rows at the start) are always kept
  - All footer rows (non-data rows at the end) are always kept
  - The first and last KEEP_DATA_ROWS data rows are kept

This ensures the LLM can determine skip_lines_start and skip_lines_end correctly.
"""

import email
import io
import logging

logger = logging.getLogger(__name__)

# A file with more than this many data rows is considered "large"
LARGE_FILE_THRESHOLD = 40
# How many data rows to keep at the start and end when truncating
KEEP_DATA_ROWS = 8
# Warn the user if the file (after processing) is still very large
WARN_SIZE_BYTES = 500_000


def process_file(content: bytes, filename: str) -> tuple[str, str | None]:
    """
    Process an uploaded file and return (text_content, warning_message).

    warning_message is None if everything is fine, or a string describing
    any truncation or size issues.
    """
    name_lower = filename.lower()

    if name_lower.endswith(".eml"):
        return _process_eml(content, filename)
    elif name_lower.endswith((".xls", ".xlsx", ".xlsm", ".xlsb")):
        return _process_xls(content, filename)
    else:
        # Treat as CSV / plain text
        return _process_csv(content, filename)


# ── CSV ───────────────────────────────────────────────────────────────────────

def _process_csv(content: bytes, filename: str) -> tuple[str, str | None]:
    # Try common encodings
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")

    lines = text.splitlines()
    total = len(lines)

    if total == 0:
        return "", None

    # Identify footer rows: trailing lines that are empty or have significantly
    # fewer fields than the modal line (likely summary/total rows)
    footer_lines, data_lines, header_lines = _split_csv_lines(lines)

    warning = None
    if len(data_lines) > LARGE_FILE_THRESHOLD:
        omitted = len(data_lines) - KEEP_DATA_ROWS * 2
        kept_data = (
            data_lines[:KEEP_DATA_ROWS]
            + [f"# ... {omitted} rows omitted (showing first {KEEP_DATA_ROWS} and last {KEEP_DATA_ROWS} data rows) ..."]
            + data_lines[-KEEP_DATA_ROWS:]
        )
        warning = (
            f"File has {total} lines. Showing header ({len(header_lines)} lines), "
            f"first/last {KEEP_DATA_ROWS} data rows, and footer ({len(footer_lines)} lines). "
            f"{omitted} middle rows were omitted."
        )
    else:
        kept_data = data_lines

    result_lines = header_lines + kept_data + footer_lines
    result = "\n".join(result_lines)

    if len(result.encode()) > WARN_SIZE_BYTES and warning is None:
        warning = f"File content is large ({len(content):,} bytes). Consider using a smaller sample if the LLM struggles."

    return result, warning


def _split_csv_lines(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Split lines into (footer_lines, data_lines, header_lines).

    Heuristic:
    - Detect the modal field count (most common non-zero column count)
    - "Data rows" are rows whose field count >= modal_count * 0.6
    - Header rows: consecutive rows at the start that are NOT data rows
    - Footer rows: consecutive rows at the end that are NOT data rows
    """
    import csv

    def field_count(line: str) -> int:
        try:
            return len(next(csv.reader([line])))
        except Exception:
            return 0

    counts = [field_count(line) for line in lines]

    # Modal count among non-zero counts
    non_zero = [c for c in counts if c > 0]
    if not non_zero:
        return [], lines, []

    from collections import Counter
    modal_count = Counter(non_zero).most_common(1)[0][0]
    threshold = max(1, int(modal_count * 0.6))

    is_data = [c >= threshold for c in counts]

    # Find first and last data row indices
    first_data = next((i for i, d in enumerate(is_data) if d), None)
    last_data = next((i for i, d in enumerate(reversed(is_data)) if d), None)

    if first_data is None:
        return [], [], lines

    last_data_idx = len(lines) - 1 - last_data

    header_lines = lines[:first_data]
    footer_lines = lines[last_data_idx + 1:]
    data_lines = lines[first_data:last_data_idx + 1]

    return footer_lines, data_lines, header_lines


# ── XLS / XLSX ────────────────────────────────────────────────────────────────

def _process_xls(content: bytes, filename: str) -> tuple[str, str | None]:
    try:
        import pandas as pd
    except ImportError:
        return f"[Could not read {filename}: pandas is not installed]", None

    buf = io.BytesIO(content)
    warnings: list[str] = []

    try:
        xl = pd.ExcelFile(buf)
    except Exception as e:
        return f"[Could not open {filename}: {e}]", None

    sheet_texts: list[str] = []

    for sheet_name in xl.sheet_names:
        try:
            df = xl.parse(sheet_name, header=None, dtype=str)
        except Exception as e:
            sheet_texts.append(f"=== Sheet: {sheet_name} ===\n[Error reading sheet: {e}]")
            continue

        # Convert to list of tab-separated rows
        rows = []
        for _, row in df.iterrows():
            rows.append("\t".join("" if pd.isna(v) else str(v) for v in row))

        footer_lines, data_lines, header_lines = _split_csv_lines(rows)

        if len(data_lines) > LARGE_FILE_THRESHOLD:
            omitted = len(data_lines) - KEEP_DATA_ROWS * 2
            kept_data = (
                data_lines[:KEEP_DATA_ROWS]
                + [f"# ... {omitted} rows omitted ..."]
                + data_lines[-KEEP_DATA_ROWS:]
            )
            warnings.append(
                f"Sheet '{sheet_name}': {len(data_lines)} data rows, showing first/last {KEEP_DATA_ROWS}."
            )
        else:
            kept_data = data_lines

        all_rows = header_lines + kept_data + footer_lines
        sheet_texts.append(f"=== Sheet: {sheet_name} ===\n" + "\n".join(all_rows))

    result = "\n\n".join(sheet_texts)
    warning = "; ".join(warnings) if warnings else None
    return result, warning


# ── EML ───────────────────────────────────────────────────────────────────────

# If the stripped email body exceeds this, warn the user to use a larger-context model.
# We do NOT truncate — dropping content risks missing the transaction data the LLM needs.
EML_BODY_WARN_CHARS = 4_000


def _strip_html(html: str) -> str:
    """Very lightweight HTML → plain text: strip tags, collapse whitespace."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _process_eml(content: bytes, filename: str) -> tuple[str, str | None]:
    try:
        msg = email.message_from_bytes(content)
    except Exception as e:
        return f"[Could not parse {filename}: {e}]", None

    parts = [
        f"From: {msg.get('from', '')}",
        f"To: {msg.get('to', '')}",
        f"Subject: {msg.get('subject', '')}",
        f"Date: {msg.get('date', '')}",
        "",
    ]

    # Prefer plain text body; fall back to HTML (stripped)
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
        body = _strip_html(body)

    warning = None
    if len(body) > EML_BODY_WARN_CHARS:
        warning = (
            f"This email's body is large ({len(body):,} characters after stripping HTML). "
            f"If the model returns a context-length error, try switching to a model with a "
            f"larger context window (8k tokens or more). The full email has been sent — nothing was removed."
        )

    parts.append(body)
    return "\n".join(parts), warning
