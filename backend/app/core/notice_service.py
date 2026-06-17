"""
Server-side advisory notice channel.

A unified surface for non-fatal server-state messages the frontend should
show in its banner stack. Today this carries:

- Ledger parse errors (previously surfaced via ``/api/ledger/errors``, now folded in here).
- Ledger advisory warnings (e.g., multi-file ledgers that use
  ``pushtag``/``pushmeta`` syntax which is preserved as effect but not as
  syntax on rewrite).

Future surfaces (background-job results, version advisories, external config
changes) plug in via new ``compute_*_notices`` functions and the same Notice
shape.

Notices are computed statelessly per request from the SQLite mirror and the
ledger file tree — no persistent server-side notice store. Dismissal lives
in the frontend session.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from typing import List, Literal, Optional, Tuple

from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning", "info"]
Source = Literal["ledger", "system", "background"]


@dataclass
class Notice:
    """A single server-side advisory.

    ``signature`` is a free-form discriminator used by the frontend to decide
    whether a previously-dismissed notice should resurface. For parse-error
    notices the signature is the error count: dismissing a 3-error notice
    then having the count change to 5 re-surfaces it. For static advisories
    the signature is a constant like ``"1"`` so dismissal sticks for the
    session.

    ``learn_more_url`` is an optional link to a docs page that describes the
    notice in detail. The frontend renders this as a "Learn more →" link in
    the banner when present.
    """
    code: str
    severity: Severity
    source: Source
    title: str
    message: str
    signature: str
    dismissible: bool = True
    details: Optional[List[str]] = None
    learn_more_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_ledger_notices(sqlite_reader: SqliteReader, ledger_root: Optional[str] = None) -> List[Notice]:
    """Build the ledger-sourced notices for the current ledger state.

    Sources today:
    - Parse errors (one summary notice with details list, signature = count).
    - Special-syntax detection (pushtag/pushmeta) — added by a separate
      helper called from here once the syntax sniffer lands.
    """
    notices: List[Notice] = []

    raw_errors = []
    try:
        raw_errors = sqlite_reader.get_errors()
    except Exception:
        logger.exception("notice_service: failed to read ledger errors")

    # A `plugin "fava.plugins.*"` directive doesn't hard-fail the load —
    # Beancount catches the ImportError per-plugin and continues (invariant
    # I9). Rather than letting it surface as a scary red parse error, split it
    # out into a friendly info Notice; the directive is left in the file so it
    # still works if the user opens the ledger in Fava.
    fava_errors, other_errors = _partition_fava_plugin_errors(raw_errors)

    if other_errors:
        details = []
        for e in other_errors:
            line = e.get("line_number", 0) or 0
            msg = e.get("message", "")
            src = e.get("source_file") or "ledger"
            details.append(f"{src}:{line} — {msg}")
        notices.append(Notice(
            code="LEDGER_PARSE_ERROR",
            severity="error",
            source="ledger",
            title=(
                f"Your ledger has {len(other_errors)} parsing "
                f"{'error' if len(other_errors) == 1 else 'errors'} "
                "that may affect app functionality."
            ),
            message="Open the details to see each error.",
            signature=str(len(other_errors)),
            dismissible=True,
            details=details,
        ))

    if fava_errors:
        plugin_names = sorted({
            name for e in fava_errors
            if (name := _extract_fava_plugin_name(e.get("message", "")))
        })
        notices.append(Notice(
            code="FAVA_PLUGIN_NOT_NEEDED",
            severity="info",
            source="ledger",
            title="A Fava plugin in your ledger isn't needed in Finzytrack.",
            message=(
                "Your documents work without it. The plugin line is left "
                "untouched, so your ledger still works if you open it in Fava."
            ),
            signature=str(len(fava_errors)),
            dismissible=True,
            details=plugin_names or None,
        ))

    if ledger_root:
        special = _detect_special_syntax(ledger_root)
        if special:
            notices.append(Notice(
                code="MULTIFILE_PUSHTAG_PUSHMETA",
                severity="warning",
                source="ledger",
                title="Your ledger uses pushtag/pushmeta block syntax.",
                message=(
                    "Finzytrack preserves the tags and metadata these blocks "
                    "apply, but replaces the block syntax with inline tags or "
                    "per-entry metadata the next time it rewrites the file."
                ),
                signature="1",
                dismissible=True,
                details=sorted(special),
                learn_more_url="https://docs.finzytrack.com/reference/ledger-rewrites/",
            ))

    return notices


_FAVA_PLUGIN_RE = re.compile(r"""importing ["'](fava\.plugins[^"']*)["']""")


def _extract_fava_plugin_name(message: str) -> Optional[str]:
    """Return the ``fava.plugins.*`` module name from a load-error message,
    or ``None`` if the message isn't a Fava-plugin import failure."""
    if not message:
        return None
    match = _FAVA_PLUGIN_RE.search(message)
    return match.group(1) if match else None


def is_fava_plugin_import_error(message: str) -> bool:
    """True if a ledger error is a ``fava.plugins.*`` import failure.

    Such errors do not break the load (Beancount catches them per-plugin), so
    callers that gate functionality on "fatal" errors should treat them as
    benign — see the query endpoint and invariant I9.
    """
    return _extract_fava_plugin_name(message) is not None


def _partition_fava_plugin_errors(raw_errors: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Split ledger errors into (fava-plugin-import errors, everything else)."""
    fava: List[dict] = []
    other: List[dict] = []
    for e in raw_errors:
        if _extract_fava_plugin_name(e.get("message", "")):
            fava.append(e)
        else:
            other.append(e)
    return fava, other


def _detect_special_syntax(ledger_root: str) -> List[str]:
    """Best-effort sniff for files in the include tree that use ``pushtag``
    or ``pushmeta`` block syntax. Returns the list of files containing it.

    Defers to the loader helper so it stays consistent with the write-path
    include traversal.
    """
    from pathlib import Path

    from app.core.ledger_loader import discover_includes_per_file

    try:
        include_map = discover_includes_per_file(ledger_root)
    except Exception:
        logger.exception("notice_service: include traversal failed")
        return []

    affected: List[str] = []
    keywords = ("pushtag ", "pushmeta ")
    for fname in include_map:
        try:
            text = Path(fname).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            # Skip comments.
            if stripped.startswith(";"):
                continue
            if any(stripped.startswith(k) for k in keywords):
                affected.append(fname)
                break
    return affected


def compute_all_notices(sqlite_reader: SqliteReader, ledger_root: Optional[str] = None) -> List[Notice]:
    """Aggregate notices from every source. The single entry point for the
    /api/notices router."""
    notices: List[Notice] = []
    notices.extend(compute_ledger_notices(sqlite_reader, ledger_root))
    # Future sources plug in here:
    # notices.extend(compute_system_notices())
    # notices.extend(compute_background_notices())
    return notices
