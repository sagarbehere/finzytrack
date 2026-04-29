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
      Column headers: 1='Date', 2='Ref No', 3='Description', 4='Debit', 5='Credit'

    Uses 1-based indices matching the file preview column headers and the rule schema.
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

    col_map = ", ".join(f"{i + 1}={c.strip()!r}" for i, c in enumerate(cols))
    return f" Column headers: {col_map}."


def _csv_parse_hint(lines: list[str]) -> str:
    """
    Compute a parse hint to prepend to the file content sent to the AI.

    The hint is a best-effort guess from a heuristic and may be wrong on
    files with unusual layouts. The AI is instructed to cross-check it
    against the column-header row visible in the file content.

    For CSV: the parser counts ALL lines (including blank ones) when applying
    skip_lines_start, so the count includes every line in the header section.
    """
    footer_lines, data_lines, header_lines = _split_csv_lines(lines)
    if not data_lines:
        return ""

    # `header_lines` already includes the column-header row (the last entry,
    # since the column header is not a transaction row and is excluded from
    # data_lines). skip_lines_start = the count of all rows up to and
    # including the column header.
    skip_start = len(header_lines)

    n_transactions = len(data_lines)
    last_tx_row = skip_start + n_transactions  # 1-based row number of the last transaction
    # Count only non-blank footer rows for the skip_lines_end hint.
    # Blank rows at the end are harmless — the date filter skips them — so they
    # should not inflate the recommended count.
    non_blank_footer = [line for line in footer_lines if line.strip()]
    footer_note = (
        f" Heuristic guess for skip_lines_end: {len(non_blank_footer)}"
        f" (last transaction at row {last_tx_row} (1-based); {len(non_blank_footer)} trailing non-blank footer rows detected)."
        if non_blank_footer else ""
    )
    col_hint = _col_header_hint(header_lines[-1] if header_lines else "")

    col_header_row = skip_start  # 1-based row number of the column-header row
    first_tx_row = skip_start + 1  # 1-based row number of the first transaction
    return (
        f"[Parse hint (best-effort guess from a heuristic — verify against the column header row visible below):"
        f" skip_lines_start ≈ {skip_start} (column-header row appears to be row {col_header_row} (1-based);"
        f" first transaction at row {first_tx_row})."
        f" Apparent transaction rows: {n_transactions}.{footer_note}"
        f"{col_hint}]"
    )


def _split_csv_lines(lines: list[str], delimiter: str = ",") -> tuple[list[str], list[str], list[str]]:
    """
    Split lines into (footer_lines, data_lines, header_lines).

    Strategy: transaction rows almost always form a contiguous block of
    rows with similar shape (same field count for CSV, similar non-empty
    cell count for XLS). We find that block, then carve off any column-
    header row at the top of it.

    Returned convention:
      - `data_lines` contains transaction rows only.
      - `header_lines` includes everything before the first transaction,
        including the column-header row (its last entry).
      - Callers that need the column-header text should read `header_lines[-1]`.

    Field counting differs by format:
      - CSV: the total number of delimiter-separated fields. Empty fields
        between commas still count, so `,,UPI/Foo,500.00` and
        `REF123,UPI/Foo,500.00` both have 4 fields and group together.
      - XLS: the count of *non-empty* cells. pandas pads every row to the
        sheet's max width, so total cell count is uniform and useless;
        non-empty count is what discriminates transactions from metadata.

    Block detection allows adjacent rows to differ by ≤1 field, so
    transactions with optional fields (e.g. NEFT with a reference number
    vs UPI with the reference column blank) still cluster into one block.

    Edge case: files with no clear block (single-transaction files, very
    short files) fall back to a row-by-row classifier requiring
    field_count ≥ 0.75×max_count and at least one pure-decimal field.

    The whole heuristic is best-effort. The result is fed to the AI as a
    *hint*; the AI is told to cross-check against the actual file content.
    """
    import csv

    def field_count(line: str) -> int:
        try:
            if delimiter == "\t":
                # XLS: count non-empty cells (pandas pads, so total is uniform).
                return sum(1 for f in line.split("\t") if f.strip())
            # CSV: total fields including empty ones. Empty cells between
            # commas still count, which keeps transactions with optional
            # fields (e.g. blank memo) in the same block as fully-populated ones.
            return len(next(csv.reader([line], delimiter=delimiter)))
        except Exception:
            return 0

    def has_decimal(line: str) -> bool:
        """True if any field parses as a pure decimal (not a date, not text)."""
        try:
            if delimiter == "\t":
                fields = line.split("\t")
            else:
                fields = next(csv.reader([line], delimiter=delimiter))
        except Exception:
            return False
        for raw in fields:
            f = raw.strip().replace(",", "").replace(" ", "")
            if not f:
                continue
            # Skip date-like and time-like fields (contain / or -).
            if "/" in f or "-" in f:
                continue
            try:
                float(f)
                return True
            except ValueError:
                continue
        return False

    if not lines:
        return [], [], []

    counts = [field_count(line) for line in lines]

    # ── Primary strategy: find the longest contiguous block of rows with
    #    similar field counts (≥ 3, with ±1 tolerance between adjacent rows).
    block_start, block_end = _find_data_block(counts)

    if block_start is not None:
        # Within the block, walk forward to find the first row containing a
        # pure decimal — that's the first transaction. Everything before it
        # inside the block is column-header rows (text labels with no decimals).
        block_data_start = block_end + 1  # default: no decimals found
        for i in range(block_start, block_end + 1):
            if has_decimal(lines[i]):
                block_data_start = i
                break

        if block_data_start <= block_end:
            header_lines = lines[:block_data_start]
            data_lines = lines[block_data_start:block_end + 1]
            footer_lines = lines[block_end + 1:]
            return footer_lines, data_lines, header_lines
        # Block had no decimals at all → fall through to the threshold heuristic.

    # ── Fallback strategy: row-by-row classification for short/single-row files.
    non_zero = [c for c in counts if c > 0]
    if not non_zero:
        return [], lines, []

    max_count = max(non_zero)
    threshold = max(3, int(max_count * 0.75))

    is_data = [c >= threshold and has_decimal(line) for c, line in zip(counts, lines)]

    first_data = next((i for i, d in enumerate(is_data) if d), None)
    if first_data is None:
        return [], [], lines

    last_data_rev = next((i for i, d in enumerate(reversed(is_data)) if d), None)
    last_data_idx = len(lines) - 1 - last_data_rev

    # In the fallback path, treat the row immediately before the first
    # transaction as the column-header row (typical of well-formed files).
    header_lines = lines[:first_data]
    footer_lines = lines[last_data_idx + 1:]
    data_lines = lines[first_data:last_data_idx + 1]

    return footer_lines, data_lines, header_lines


def _find_data_block(field_counts: list[int]) -> tuple[int | None, int | None]:
    """
    Find the longest contiguous run of rows whose field counts are all ≥ 3
    and where each adjacent pair differs by at most 1.

    The ±1 tolerance lets transactions with optional fields (e.g. UPI with
    blank memo vs NEFT with populated reference) stay in the same block.

    Tiebreak: among runs of equal length, prefer the one with the higher
    average field count.

    Returns `(start, end)` inclusive, or `(None, None)` if no run of
    length ≥ 2 qualifies.
    """
    n = len(field_counts)
    if n == 0:
        return None, None

    best_start: int | None = None
    best_end: int | None = None
    best_score: tuple[int, float] = (0, 0.0)

    i = 0
    while i < n:
        if field_counts[i] < 3:
            i += 1
            continue
        j = i + 1
        while j < n and field_counts[j] >= 3 and abs(field_counts[j] - field_counts[j - 1]) <= 1:
            j += 1
        run_len = j - i
        if run_len >= 2:
            avg_fc = sum(field_counts[i:j]) / run_len
            score = (run_len, avg_fc)
            if score > best_score:
                best_score = score
                best_start = i
                best_end = j - 1
        i = j if j > i else i + 1

    return best_start, best_end


def _xls_parse_hint(rows: list[str]) -> str:
    """
    Compute a parse hint for an XLS sheet.

    The hint is a best-effort guess from a heuristic and may be wrong on
    sheets with unusual layouts. The AI is instructed to cross-check it
    against the column-header row visible in the file content.

    For XLS: the XLSX library does NOT strip blank rows before applying
    skip_lines_start, so the count includes ALL rows (blank or not).
    """
    footer_lines, data_lines, header_lines = _split_csv_lines(rows, delimiter="\t")
    if not data_lines:
        return ""

    # XLSX library preserves all rows (including blank), so skip_lines_start
    # counts all rows — not just non-blank ones. With the new convention,
    # `header_lines` already includes the column-header row as its last entry.
    skip_start = len(header_lines)

    n_transactions = len(data_lines)
    last_tx_row = skip_start + n_transactions  # 1-based row number of the last transaction
    # XLS: count ALL trailing rows (blank + non-blank). The XLS parser slices
    # the row array via dataRows.slice(0, -skipEnd), which counts every row
    # including blanks — so the hint must match that semantics. (Earlier
    # versions counted non-blank only, producing an off-by-N error whenever
    # the footer contained blank rows.)
    if footer_lines:
        non_blank_footer = sum(1 for line in footer_lines if line.strip())
        first_footer_row = last_tx_row + 1
        last_footer_row = last_tx_row + len(footer_lines)
        footer_note = (
            f" Heuristic guess for skip_lines_end: {len(footer_lines)}"
            f" (last transaction at row {last_tx_row} (1-based); footer is rows {first_footer_row}–{last_footer_row},"
            f" {len(footer_lines)} rows of which {non_blank_footer} are non-blank — XLS footer rows may contain"
            f" numeric data that would be imported as fake transactions; set skip_lines_end explicitly)."
        )
    else:
        footer_note = (
            f" Heuristic guess for skip_lines_end: 0 (last transaction at row {last_tx_row} (1-based);"
            f" no trailing footer rows detected; verify by checking the last rows of the file)."
        )
    col_hint = _col_header_hint(header_lines[-1] if header_lines else "", delimiter="\t")

    col_header_row = skip_start
    first_tx_row = skip_start + 1
    return (
        f"[Parse hint (best-effort guess from a heuristic — verify against the column header row visible below):"
        f" skip_lines_start ≈ {skip_start} (column-header row appears to be row {col_header_row} (1-based);"
        f" first transaction at row {first_tx_row})."
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
