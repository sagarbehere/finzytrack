"""Append-only audit log for AI assistant validation failures.

Every time a tool returns validation errors to the model, a single JSONL line
is appended to ``./data/ai_diagnostics.jsonl`` recording:

  - timestamp (ISO 8601)
  - tool name
  - the error count, the first 5 field paths, and any hint markers
  - optional recipe_id / filename for downstream correlation

The file rotates automatically once it exceeds ``DIAGNOSTICS_MAX_BYTES``;
the previous file is renamed with a ``.1`` suffix and ``DIAGNOSTICS_BACKUPS``
generations are kept (matching the main app's RotatingFileHandler approach).
``scripts/ai_audit.py`` reads the active file plus all rotated backups.

This module fails open: any I/O error during recording is logged and
swallowed so a diagnostics-write failure can never break a tool call.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DIAGNOSTICS_PATH = Path("./data/ai_diagnostics.jsonl")
DIAGNOSTICS_MAX_BYTES = 5 * 1024 * 1024   # 5 MB — same default as the main log
DIAGNOSTICS_BACKUPS = 3                   # keep .1 .2 .3 then drop the oldest
_HINT_RE = re.compile(r"Hint:\s*(.+?)(?:\.\s*$|$)")
_FIELD_RE = re.compile(r"^([^:]+):")


def _rotate_if_needed(path: Path) -> None:
    """Rename the active file to .1, shifting older backups by one. Idempotent."""
    try:
        if not path.is_file() or path.stat().st_size < DIAGNOSTICS_MAX_BYTES:
            return
    except OSError:
        return

    # Drop the oldest backup, then shift each .N -> .(N+1) downward.
    oldest = path.with_suffix(path.suffix + f".{DIAGNOSTICS_BACKUPS}")
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError:
            pass
    for i in range(DIAGNOSTICS_BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        dst = path.with_suffix(path.suffix + f".{i + 1}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError:
                pass
    # Finally, the active file becomes .1
    try:
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def rotated_paths() -> list[Path]:
    """Return the active path plus any existing rotated backups, newest first.

    Used by scripts/ai_audit.py so an aggregation across all-time spans the
    rotation boundary without missing records.
    """
    out: list[Path] = []
    if DIAGNOSTICS_PATH.is_file():
        out.append(DIAGNOSTICS_PATH)
    for i in range(1, DIAGNOSTICS_BACKUPS + 1):
        backup = DIAGNOSTICS_PATH.with_suffix(DIAGNOSTICS_PATH.suffix + f".{i}")
        if backup.is_file():
            out.append(backup)
    return out


def _extract_hint(error_msg: str) -> str | None:
    m = _HINT_RE.search(error_msg)
    return m.group(1).strip() if m else None


def _extract_field(error_msg: str) -> str | None:
    m = _FIELD_RE.match(error_msg)
    return m.group(1).strip() if m else None


def record_validation_failure(
    tool: str,
    errors: Iterable[str],
    *,
    recipe_id: str | None = None,
    filename: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append a single JSONL record to the audit log. Never raises."""
    err_list = list(errors)
    if not err_list:
        return

    fields = [_extract_field(e) for e in err_list[:5]]
    hints = [h for h in (_extract_hint(e) for e in err_list) if h]

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "error_count": len(err_list),
        "first_fields": [f for f in fields if f],
        "hints": hints,
    }
    if recipe_id is not None:
        record["recipe_id"] = recipe_id
    if filename is not None:
        record["filename"] = filename
    if extra:
        record.update(extra)

    try:
        DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(DIAGNOSTICS_PATH)
        with DIAGNOSTICS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Failed to record AI diagnostics for %s: %s", tool, e)
