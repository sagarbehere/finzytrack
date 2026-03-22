"""
File processor for the AI assistant.

Reads uploaded CSV, XLS/XLSX, and EML files and returns a text representation
suitable for inclusion in an LLM prompt.

For genuinely large CSV/XLS files (> SMALL_FILE_LINES total lines) the middle
transaction rows are omitted, but:
  - All header rows (non-data rows at the start) are always kept
  - All footer rows (non-data rows at the end) are always kept
  - The first and last KEEP_DATA_ROWS data rows are kept

Small files are sent in full — the row-classification heuristic is only applied
when the file is large enough that truncation is clearly necessary.
"""

import email
import io
import logging

logger = logging.getLogger(__name__)

# Files with at most this many total lines are sent verbatim (no truncation).
# Row-classification heuristics are only applied beyond this point, where the
# risk of misclassifying footer/legend rows is outweighed by the need to trim.
SMALL_FILE_LINES = 200
# How many data rows to keep at the start and end when truncating large files
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

    hint = _csv_parse_hint(lines)
    warning = None

    if total <= SMALL_FILE_LINES:
        # Small file: send everything as-is. No heuristic classification needed.
        result = "\n".join(lines)
    else:
        # Large file: classify rows and truncate the middle data rows.
        footer_lines, data_lines, header_lines = _split_csv_lines(lines)

        if len(data_lines) > KEEP_DATA_ROWS * 2:
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

        result = "\n".join(header_lines + kept_data + footer_lines)

    if hint:
        result = hint + "\n\n" + result

    if len(result.encode()) > WARN_SIZE_BYTES and warning is None:
        warning = f"File content is large ({len(content):,} bytes). Consider using a smaller sample if the LLM struggles."

    return result, warning


def _col_header_hint(header_row: str, delimiter: str = ",") -> str:
    """
    Return a compact column-index map from the column header row.

    Example output:
      Column headers: 0='Date', 1='Ref No', 2='Description', 3='Debit', 4='Credit'

    This lets the AI look up column names → indices directly without counting
    delimiters in the raw file content.
    """
    import csv
    try:
        if delimiter == "\t":
            cols = header_row.split("\t")
        else:
            cols = next(csv.reader([header_row], delimiter=delimiter))
    except Exception:
        return ""

    # Drop trailing empty columns (common in XLS exports)
    while cols and not cols[-1].strip():
        cols.pop()

    if not cols:
        return ""

    col_map = ", ".join(f"{i}={c.strip()!r}" for i, c in enumerate(cols))
    return f" Column headers: {col_map}."


def _csv_parse_hint(lines: list[str]) -> str:
    """
    Compute a parse hint to prepend to the file content sent to the AI.

    Tells the AI the recommended skip_lines_start and skip_lines_end values
    based on the detected file structure, so it doesn't need to count manually.

    For CSV: PapaParse strips blank lines before applying skip_lines_start, so
    the count is non-blank lines only (header section + column header row).
    """
    footer_lines, data_lines, header_lines = _split_csv_lines(lines)
    if not data_lines:
        return ""

    # PapaParse (frontend CSV parser) removes blank lines before applying
    # skip_lines_start, so count only non-blank lines in the header section.
    non_blank_header = sum(1 for l in header_lines if l.strip())
    # +1 for the column header row (data_lines[0]), which must also be skipped.
    skip_start = non_blank_header + 1

    n_transactions = len(data_lines) - 1  # subtract the column header row
    footer_note = (
        f" {len(footer_lines)} trailing rows detected"
        " (non-date content is auto-filtered by the parser)."
        if footer_lines else ""
    )
    col_hint = _col_header_hint(data_lines[0])

    return (
        f"[Parse hint: recommended skip_lines_start: {skip_start}"
        f" ({non_blank_header} non-blank metadata lines + 1 column header row)."
        f" Apparent transaction rows: {n_transactions}."
        f" Recommended skip_lines_end: 0 (rows without a parseable date are auto-skipped).{footer_note}"
        f"{col_hint}]"
    )


def _split_csv_lines(lines: list[str], delimiter: str = ",") -> tuple[list[str], list[str], list[str]]:
    """
    Split lines into (footer_lines, data_lines, header_lines).

    Heuristic (only applied to large files):
    - Detect the maximum field count across all non-empty rows
    - "Data rows" are rows whose field count >= max_count * 0.6
    - Header rows: consecutive rows at the start that are NOT data rows
    - Footer rows: consecutive rows at the end that are NOT data rows

    Using max_count (rather than modal count) ensures that footer/legend rows
    with fewer fields than transactions don't lower the threshold and get
    misclassified as data.
    """
    import csv

    def field_count(line: str) -> int:
        try:
            return len(next(csv.reader([line], delimiter=delimiter)))
        except Exception:
            return 0

    counts = [field_count(line) for line in lines]

    non_zero = [c for c in counts if c > 0]
    if not non_zero:
        return [], lines, []

    max_count = max(non_zero)
    # Minimum threshold of 2: single-column rows are almost never transaction
    # data in financial CSVs — they're metadata, footnotes, or legend entries.
    threshold = max(2, int(max_count * 0.6))

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


def _xls_parse_hint(rows: list[str]) -> str:
    """
    Compute a parse hint for an XLS sheet.

    For XLS: the XLSX library does NOT strip blank rows before applying
    skip_lines_start, so the count includes ALL rows (blank or not).
    """
    footer_lines, data_lines, header_lines = _split_csv_lines(rows, delimiter="\t")
    if not data_lines:
        return ""

    # XLSX library preserves all rows (including blank), so skip_lines_start
    # counts all rows — not just non-blank ones.
    skip_start = len(header_lines) + 1  # +1 for column header row (data_lines[0])

    n_transactions = len(data_lines) - 1
    if footer_lines:
        footer_note = (
            f" Recommended skip_lines_end: {len(footer_lines)}"
            f" ({len(footer_lines)} trailing rows detected — XLS footer rows may contain"
            f" numeric data that would be imported as fake transactions; set skip_lines_end explicitly)."
        )
    else:
        footer_note = " Recommended skip_lines_end: 0 (no trailing footer rows detected; verify by checking the last rows of the file)."
    col_hint = _col_header_hint(data_lines[0], delimiter="\t")

    return (
        f"[Parse hint: recommended skip_lines_start: {skip_start}"
        f" ({len(header_lines)} header rows including blanks + 1 column header row)."
        f" Apparent transaction rows: {n_transactions}.{footer_note}"
        f"{col_hint}]"
    )


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

        hint = _xls_parse_hint(rows)

        if len(rows) <= SMALL_FILE_LINES:
            # Small sheet: send everything as-is.
            kept_rows = rows
        else:
            footer_lines, data_lines, header_lines = _split_csv_lines(rows, delimiter="\t")

            if len(data_lines) > KEEP_DATA_ROWS * 2:
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

            kept_rows = header_lines + kept_data + footer_lines

        sheet_content = "\n".join(kept_rows)
        if hint:
            sheet_content = hint + "\n\n" + sheet_content
        sheet_texts.append(f"=== Sheet: {sheet_name} ===\n" + sheet_content)

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
