"""
Pydantic schemas for ledger export operations (DuckDB, etc.).
"""
from typing import Optional
from pydantic import BaseModel, Field


class DuckDBExportRequest(BaseModel):
    """Request to export ledger to DuckDB."""
    force: bool = Field(default=False, description="Force regeneration even if up-to-date")


class DuckDBExportData(BaseModel):
    """DuckDB export result data."""
    postings_count: int = Field(..., description="Number of postings exported")
    transactions_count: int = Field(..., description="Number of transactions exported")
    duration_ms: int = Field(..., description="Export duration in milliseconds")
    path: str = Field(..., description="Path to DuckDB file")
    last_sync: str = Field(..., description="Last sync timestamp (ISO format)")


class DuckDBStatusData(BaseModel):
    """DuckDB export status data."""
    exists: bool = Field(..., description="Whether DuckDB file exists")
    path: str = Field(..., description="Path to DuckDB file")
    size_bytes: int = Field(..., description="File size in bytes")
    last_modified: Optional[str] = Field(None, description="Last modified timestamp (ISO format)")
    postings_count: int = Field(default=0, description="Number of postings in database")
    is_current: bool = Field(default=False, description="Whether DuckDB is up-to-date with ledger")
    ledger_modified: Optional[str] = Field(None, description="Ledger file modification timestamp")
