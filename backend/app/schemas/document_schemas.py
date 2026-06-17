"""
Pydantic request/response schemas for the documents feature.

Covers the shared file-storage backend (upload), account-level ``Document``
directives, and the orphan sweep. Transaction-level documents ride the existing
transaction update path (``document*`` metadata) and need no schema here.
"""

import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentUploadData(BaseModel):
    """Returned by the upload endpoint. ``path`` is the exact ledger-relative
    string to stage into a transaction's ``document`` metadata or to pass to
    the account-document create endpoint."""
    path: str = Field(..., description="Ledger-relative path to the stored file")
    full_hash: str = Field(..., description="Full SHA-256 hex of the file content")
    size: int = Field(..., description="File size in bytes")
    display_name: str = Field(..., description="Stored filename (basename) for display")


class DocumentDetails(BaseModel):
    """An account document (a ``Document`` directive)."""
    date: datetime.date
    account: str
    path: str = Field(..., description="Ledger-relative path (use with the serve endpoint)")
    display_name: str = Field(..., description="Filename basename for display")
    tags: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentListData(BaseModel):
    documents: List[DocumentDetails]


class AccountDocumentCreateRequest(BaseModel):
    account: str = Field(..., description="Account the document belongs to")
    date: datetime.date = Field(..., description="Document date")
    path: str = Field(..., description="Ledger-relative path returned by the upload endpoint")
    tags: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None


class AccountDocumentCreateData(BaseModel):
    created: bool
    message: str


class AccountDocumentDeleteRequest(BaseModel):
    account: str = Field(..., description="Account the document belongs to")
    path: str = Field(..., description="Ledger-relative path of the document to detach")


class AccountDocumentDeleteData(BaseModel):
    deleted_count: int
    message: str


class OrphanCandidateData(BaseModel):
    path: str = Field(..., description="Ledger-relative path")
    display_name: str
    size: int
    modified: str = Field(..., description="ISO-8601 last-modified timestamp")


class OrphanScanData(BaseModel):
    orphans: List[OrphanCandidateData]
    grace_seconds: int = Field(..., description="Files younger than this were excluded")


class OrphanDeleteRequest(BaseModel):
    paths: List[str] = Field(..., description="Ledger-relative paths selected for deletion")


class OrphanDeleteData(BaseModel):
    deleted: List[str]
    skipped: List[str] = Field(
        ..., description="Paths that became referenced, escaped the root, or could not be removed"
    )
    message: str
