"""
Documents router — shared file-storage backend plus account-level documents
and the orphan sweep.

Endpoints (prefix ``/api/documents``):
- ``POST /upload``          store an uploaded file, return its ledger-relative path
- ``GET  /file``            stream a stored document for viewing/download
- ``GET  /account``         list an account's ``Document`` directives
- ``POST /account``         attach a document to an account (Document directive)
- ``DELETE /account``       detach an account document
- ``POST /orphans/scan``    list orphan files (grace-window aware)
- ``POST /orphans/delete``  delete selected orphans (re-validated)

Transaction-level documents are *not* here: they ride the existing transaction
update path as ``document*`` metadata. This router only provides the upload/serve
backend they share.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, Body
from fastapi.responses import FileResponse

from app.schemas.response_schemas import ApiResponse
from app.schemas.document_schemas import (
    DocumentUploadData,
    DocumentListData,
    AccountDocumentCreateRequest,
    AccountDocumentCreateData,
    AccountDocumentDeleteRequest,
    AccountDocumentDeleteData,
    OrphanCandidateData,
    OrphanScanData,
    OrphanDeleteRequest,
    OrphanDeleteData,
)
from app.dependencies import (
    get_beancount_manager,
    get_config_manager,
    get_sqlite_reader,
)
from app.core.ledger_manager import LedgerManager
from app.core.config_manager import ConfigManager
from app.core.document_store import DocumentStore, DEFAULT_GRACE_SECONDS
from app.services.sqlite_reader import SqliteReader
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

#: Max upload size. Mirrors the importer's precedent (llm_parse).
_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


def _store_for(beancount_manager: LedgerManager, config_manager: ConfigManager) -> DocumentStore:
    config = config_manager.get_config()
    root = beancount_manager.resolve_documents_root(config.documents_dir)
    ledger_dir = Path(config.ledger_file).resolve().parent
    return DocumentStore(root, ledger_dir)


@router.post("/upload", response_model=ApiResponse[DocumentUploadData], operation_id="uploadDocument")
async def upload_document(
    file: UploadFile = File(...),
    date: Optional[str] = Form(None, description="Document date (YYYY-MM-DD); defaults to today"),
    narration: Optional[str] = Form(None, description="Slug source (transaction narration/payee)"),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Store an uploaded file and return its ledger-relative path + full hash.

    The path is staged client-side into a draft transaction's ``document``
    metadata (written on save) or passed to ``POST /account``. Any file type is
    accepted up to the size cap.
    """
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise APIError("Uploaded file is empty.", code=ec.EMPTY_FILE, status_code=400)
    if len(file_bytes) > _MAX_FILE_SIZE:
        raise APIError(
            f"File is too large ({len(file_bytes) / 1024 / 1024:.1f} MB). "
            f"Maximum size is {_MAX_FILE_SIZE / 1024 / 1024:.0f} MB.",
            code=ec.FILE_TOO_LARGE,
            status_code=400,
        )

    if date:
        try:
            date_obj = datetime.fromisoformat(date).date()
        except ValueError:
            raise APIError(
                f"Invalid date: {date!r} (expected YYYY-MM-DD)",
                code=ec.VALIDATION_ERROR,
                status_code=400,
            )
    else:
        date_obj = datetime.now().date()

    store = _store_for(beancount_manager, config_manager)
    stored = store.store(
        file_bytes=file_bytes,
        original_filename=file.filename or "document",
        date_obj=date_obj,
        slug_source=narration,
    )

    # Auto-write option "documents" on first use so Fava auto-discovery resolves
    # to the same root. Idempotent — a no-op once the option exists.
    try:
        beancount_manager.ensure_documents_option(store.documents_root)
    except Exception:
        logger.exception("Failed to ensure option \"documents\"; upload still succeeded")

    return success_json_response(DocumentUploadData(
        path=stored.path,
        full_hash=stored.full_hash,
        size=stored.size,
        display_name=stored.absolute_path.name,
    ))


@router.get("/file", operation_id="serveDocument")
async def serve_document(
    path: str = Query(..., description="Ledger-relative path of the stored document"),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Stream a stored document. Path-safe: traversal/absolute paths and symlink
    escapes are rejected (invariant I10)."""
    store = _store_for(beancount_manager, config_manager)
    abs_path = store.resolve(path)  # raises 403 on escape
    if not abs_path.is_file():
        raise APIError(
            f"Document not found: {path}",
            code=ec.DOCUMENT_NOT_FOUND,
            status_code=404,
        )
    return FileResponse(str(abs_path), filename=abs_path.name)


@router.get("/account", response_model=ApiResponse[DocumentListData], operation_id="listAccountDocuments")
async def list_account_documents(
    account: str = Query(..., description="Account to list documents for"),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """List the ``Document`` directives attached to an account."""
    documents = sqlite_reader.get_documents(account=account)
    return success_json_response(DocumentListData(documents=documents))


@router.post("/account", response_model=ApiResponse[AccountDocumentCreateData], operation_id="createAccountDocument")
async def create_account_document(
    request: AccountDocumentCreateRequest = Body(...),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Attach a document to an account via a ``Document`` directive."""
    # Path safety: the stored path must resolve inside the documents root.
    store = _store_for(beancount_manager, config_manager)
    store.resolve(request.path)  # raises 403 on escape

    try:
        beancount_manager.create_document_attachment(
            date_obj=request.date,
            account_name=request.account,
            filename=request.path,
            tags=request.tags,
            links=request.links,
            metadata=request.metadata,
        )
    except ValueError as e:
        raise APIError(str(e), code=ec.ACCOUNT_NOT_FOUND, status_code=404)

    return success_json_response(AccountDocumentCreateData(
        created=True,
        message=f"Document attached to {request.account}",
    ))


@router.delete("/account", response_model=ApiResponse[AccountDocumentDeleteData], operation_id="deleteAccountDocument")
async def delete_account_document(
    request: AccountDocumentDeleteRequest = Body(...),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Detach an account document (remove its ``Document`` directive). The file
    on disk is left in place — use the orphan sweep to remove unreferenced
    files."""
    removed = beancount_manager.delete_document_directive(
        account_name=request.account, filename=request.path
    )
    if removed == 0:
        raise APIError(
            f"No document directive found for {request.account}: {request.path}",
            code=ec.DOCUMENT_NOT_FOUND,
            status_code=404,
        )
    return success_json_response(AccountDocumentDeleteData(
        deleted_count=removed,
        message=f"Detached {removed} document(s) from {request.account}",
    ))


@router.post("/orphans/scan", response_model=ApiResponse[OrphanScanData], operation_id="scanOrphanDocuments")
async def scan_orphan_documents(
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """List files in the documents root referenced by nothing in the ledger and
    older than the grace window."""
    store = _store_for(beancount_manager, config_manager)
    candidates = beancount_manager.scan_orphan_documents(
        documents_root=store.documents_root,
        grace_seconds=DEFAULT_GRACE_SECONDS,
    )
    orphans = [
        OrphanCandidateData(
            path=c.path,
            display_name=Path(c.path).name,
            size=c.size,
            modified=datetime.fromtimestamp(c.mtime).isoformat(),
        )
        for c in candidates
    ]
    return success_json_response(OrphanScanData(
        orphans=orphans,
        grace_seconds=int(DEFAULT_GRACE_SECONDS),
    ))


@router.post("/orphans/delete", response_model=ApiResponse[OrphanDeleteData], operation_id="deleteOrphanDocuments")
async def delete_orphan_documents(
    request: OrphanDeleteRequest = Body(...),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Delete selected orphan files. Each is re-validated as still-orphaned
    against the current ledger immediately before unlinking; any that became
    referenced (or escaped the root) are reported in ``skipped``. Deleted files
    remain recoverable from git history."""
    store = _store_for(beancount_manager, config_manager)
    outcome = beancount_manager.delete_orphan_documents(
        documents_root=store.documents_root,
        paths=request.paths,
    )
    return success_json_response(OrphanDeleteData(
        deleted=outcome.deleted,
        skipped=outcome.skipped,
        message=f"Deleted {len(outcome.deleted)} file(s); skipped {len(outcome.skipped)}",
    ))
