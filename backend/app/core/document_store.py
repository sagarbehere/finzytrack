"""
DocumentStore — file-storage backend shared by transaction- and account-level
document attachments.

Responsibilities (pure-ish file I/O; no ledger writes):
- Resolve the documents root, respecting the ledger's ``option "documents"``
  and falling back to ``config.documents_dir`` (``data/documents/``).
- Hybrid, human-readable, collision-proof naming:
  ``YYYY-MM-DD-<slug>-<short8hash>.<ext>`` sharded under ``documents/YYYY/``
  (see dev-docs/documents.md — invariant I1).
- Store uploaded bytes (idempotent for identical content + logical name).
- Path-safe resolution of a stored document for serving (invariant I10).
- Orphan scanning and deletion with the grace-window and re-validation
  guardrails (invariants I7, I8).

Paths that travel in the ledger (``document*`` metadata values and
``Document`` directive filenames) are stored **relative to the ledger file's
directory**, which is exactly how Beancount and Fava resolve them. This module
is the single place that converts between the three path forms:

  - absolute on disk:        ``<root>/data/documents/2026/<name>``
  - ledger-relative (stored): ``../documents/2026/<name>``
  - documents-root-relative:  ``2026/<name>``
"""

from __future__ import annotations

import os
import re
import hashlib
import logging
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.exceptions import APIError
from app import error_codes as ec
from app.helpers.path_guard import guard_path

logger = logging.getLogger(__name__)

#: Max length of the slug component of a stored filename. Keeps total path
#: length sane while leaving the date prefix and hash suffix intact.
SLUG_MAX_LEN = 60

#: Default grace window: files younger than this are never flagged as orphans,
#: so an in-flight upload for an unsaved draft is safe. See invariant I8.
DEFAULT_GRACE_SECONDS = 24 * 60 * 60


# ── Naming ───────────────────────────────────────────────────────────────────

def _slugify(text: Optional[str]) -> str:
    """Lowercase, ASCII, dash-cased slug. Empty string when nothing survives."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")


def _strip_leading_date(text: str) -> str:
    """Drop a leading ``YYYY-MM-DD`` (and any trailing separators) so a source
    filename that already starts with a date doesn't get double-prefixed."""
    return re.sub(r"^\d{4}-\d{2}-\d{2}[ _-]*", "", text)


def _extension(original_filename: str) -> str:
    """Original extension, lowercased, including the dot (``""`` if none)."""
    suffix = Path(original_filename).suffix.lower()
    # Guard against absurd "extensions" (e.g. a trailing ".something with spaces")
    if suffix and re.fullmatch(r"\.[a-z0-9]+", suffix):
        return suffix
    return ""


def build_document_name(
    *,
    date_obj: date,
    slug_source: Optional[str],
    original_filename: str,
    full_hash: str,
) -> str:
    """Compose ``YYYY-MM-DD-<slug>-<short8hash>.<ext>`` (invariant I1).

    Slug fallback chain: ``slug_source`` (narration/payee) → the original
    filename stem (leading date stripped) → the literal ``document``.
    """
    slug = _slugify(slug_source)
    if not slug:
        slug = _slugify(_strip_leading_date(Path(original_filename).stem))
    if not slug:
        slug = "document"
    slug = slug[:SLUG_MAX_LEN].strip("-") or "document"

    ext = _extension(original_filename)
    short = full_hash[:8]
    return f"{date_obj.isoformat()}-{slug}-{short}{ext}"


# ── Referenced-set collection (highest-risk: a miss deletes live data) ─────────

def collect_referenced_paths(entries: List[Any]) -> Set[Path]:
    """Absolute, resolved paths referenced by the *entire* loaded ledger.

    Two sources (invariant I7):
      - every metadata key starting with ``document`` on every entry (the
        transaction-level scheme), resolved relative to the entry's own
        source-file directory; and
      - every ``Document`` directive's ``filename`` (Beancount has already
        absolutized these at load time).

    Returns resolved absolute ``Path`` objects so the orphan scan can compare
    by identity regardless of relative-vs-absolute spelling.
    """
    from beancount.core import data as bd

    referenced: Set[Path] = set()

    for entry in entries:
        meta = getattr(entry, "meta", None) or {}
        source_file = meta.get("filename")
        base_dir = Path(source_file).parent if source_file else None

        for key, value in meta.items():
            if not (isinstance(key, str) and key.startswith("document")):
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            referenced.add(_resolve_ref(value, base_dir))

        if isinstance(entry, bd.Document) and entry.filename:
            referenced.add(_resolve_ref(entry.filename, base_dir))

    return referenced


def _resolve_ref(value: str, base_dir: Optional[Path]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute() and base_dir is not None:
        candidate = base_dir / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate


# ── Documents-root resolution ──────────────────────────────────────────────────

def resolve_documents_root(
    *,
    ledger_file: str,
    options: Optional[Dict[str, Any]],
    default_dir: str,
) -> Path:
    """Resolve the documents storage root.

    Precedence (decision #1 in dev-docs/documents.md): the ledger's
    ``option "documents"`` (first entry, resolved relative to the ledger dir if
    relative) wins; otherwise the config default (``data/documents/``).
    """
    docs_opt = (options or {}).get("documents") or []
    ledger_dir = Path(ledger_file).resolve().parent
    if docs_opt:
        candidate = Path(docs_opt[0])
        if not candidate.is_absolute():
            candidate = ledger_dir / candidate
        return candidate.resolve()
    # No option set: a per-ledger subfolder (keyed by the ledger filename stem)
    # under the default parent. Different ledgers therefore never share a
    # documents folder — which matters because the orphan sweep is scoped to one
    # ledger's root, and a shared folder would let ledger B's sweep flag (and
    # offer to delete) ledger A's files. The auto-written option records this
    # choice in the ledger so it survives even if the default ever changes.
    return (Path(default_dir) / Path(ledger_file).stem).resolve()


def documents_option_value(*, documents_root: Path, ledger_file: str) -> str:
    """The string to write as ``option "documents" "<value>"`` so Fava
    auto-discovery resolves to the same root we use — i.e. the documents root
    expressed relative to the ledger file's directory."""
    ledger_dir = Path(ledger_file).resolve().parent
    return os.path.relpath(documents_root.resolve(), ledger_dir)


# ── Store ──────────────────────────────────────────────────────────────────────

@dataclass
class StoredDocument:
    """Result of storing an uploaded file."""
    path: str          # ledger-relative path (the exact string written to the ledger)
    full_hash: str     # full SHA-256 hex of the content (for a future dup-finder)
    size: int          # bytes
    absolute_path: Path


@dataclass
class OrphanCandidate:
    path: str          # ledger-relative path
    size: int
    mtime: float       # epoch seconds


@dataclass
class DeleteOutcome:
    deleted: List[str]
    skipped: List[str]  # re-validated as referenced, escaped the jail, or errored


class DocumentStore:
    """File operations rooted at a single documents root.

    Constructed with the resolved ``documents_root`` and the ``ledger_dir``
    used to express stored paths relative to the ledger (Fava-compatible).
    """

    def __init__(self, documents_root: Path, ledger_dir: Path):
        self.documents_root = Path(documents_root).resolve()
        self.ledger_dir = Path(ledger_dir).resolve()

    # -- path conversions --

    def to_ledger_relative(self, absolute_path: Path) -> str:
        """Absolute on-disk path → the ledger-relative string we store."""
        rel = os.path.relpath(Path(absolute_path).resolve(), self.ledger_dir)
        return rel.replace(os.sep, "/")

    def resolve(self, ledger_relative_path: str) -> Path:
        """Ledger-relative stored path → absolute path, path-safe (I10).

        Rejects absolute inputs outright; everything else is resolved against
        the ledger directory and guarded to stay within the documents root
        (``..`` that escapes, symlink escapes → 403).
        """
        candidate = Path(ledger_relative_path)
        if candidate.is_absolute():
            raise APIError(
                message="Absolute document paths are not allowed",
                code=ec.INVALID_PATH,
                status_code=403,
            )
        return guard_path(self.ledger_dir / candidate, self.documents_root, "document path")

    # -- store --

    def store(
        self,
        *,
        file_bytes: bytes,
        original_filename: str,
        date_obj: date,
        slug_source: Optional[str] = None,
    ) -> StoredDocument:
        """Persist ``file_bytes`` under the hybrid name; idempotent for the
        same content + logical name (same date + slug + content hash → same
        filename, no duplicate-with-suffix)."""
        full_hash = hashlib.sha256(file_bytes).hexdigest()
        name = build_document_name(
            date_obj=date_obj,
            slug_source=slug_source,
            original_filename=original_filename,
            full_hash=full_hash,
        )
        year = f"{date_obj.year:04d}"
        abs_path = self.documents_root / year / name
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        # Idempotent: the content hash is baked into the name, so an existing
        # file at this path is byte-identical by construction — skip the rewrite.
        if not abs_path.exists():
            abs_path.write_bytes(file_bytes)

        return StoredDocument(
            path=self.to_ledger_relative(abs_path),
            full_hash=full_hash,
            size=len(file_bytes),
            absolute_path=abs_path,
        )

    # -- orphan sweep --

    def scan_orphans(self, *, referenced: Set[Path]) -> List[OrphanCandidate]:
        """All files under the root referenced by nothing (invariant I7).
        ``referenced`` must be resolved absolute paths (use
        ``collect_referenced_paths``).

        Each candidate carries its ``mtime`` so the UI can distinguish recently
        modified files (within the grace window — likely an in-flight draft
        upload) from older ones. The grace window is *informational only*: it no
        longer hard-excludes files here, because that silently hid genuinely
        orphaned files the user had just dereferenced. The real safety guarantee
        is the delete-time re-validation in ``delete`` (a referenced file is
        never removed). See dev-docs/documents.md (orphan sweep).
        """
        if not self.documents_root.exists():
            return []
        candidates: List[OrphanCandidate] = []
        for path in sorted(self.documents_root.rglob("*")):
            if not path.is_file():
                continue  # also skips broken symlinks (is_file() follows links)
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in referenced:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            candidates.append(OrphanCandidate(
                path=self.to_ledger_relative(path),
                size=st.st_size,
                mtime=st.st_mtime,
            ))
        return candidates

    def delete(self, *, paths: List[str], referenced: Set[Path]) -> DeleteOutcome:
        """Delete the given ledger-relative paths, re-validating each is still
        an orphan immediately before unlinking (invariant I8).

        A path that escapes the jail, is now referenced, or no longer exists is
        skipped (deleting an already-deleted file is a no-op, not an error).
        """
        deleted: List[str] = []
        skipped: List[str] = []
        for rel in paths:
            try:
                abs_path = self.resolve(rel)
            except APIError:
                skipped.append(rel)
                continue
            if abs_path.resolve() in referenced:
                skipped.append(rel)  # became referenced between scan and delete
                continue
            try:
                abs_path.unlink(missing_ok=True)
                deleted.append(rel)
            except OSError:
                logger.exception("Failed to delete orphan document: %s", rel)
                skipped.append(rel)
        return DeleteOutcome(deleted=deleted, skipped=skipped)
