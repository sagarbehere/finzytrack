"""
Pydantic schemas for ledger export operations (DuckDB, SQLite, etc.).
"""
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field


class DatabaseType(str, Enum):
    """Supported database types for export"""
    DUCKDB = "duckdb"
    SQLITE = "sqlite"


class ExportRequest(BaseModel):
    """Request to export ledger to a database."""
    force: bool = Field(
        default=False,
        description="Force regeneration even if up-to-date"
    )
    db_type: Optional[DatabaseType] = Field(
        None,
        description="Database type to export to. If not specified, uses active database from config."
    )


class DuckDBExportRequest(BaseModel):
    """Request to export ledger to DuckDB (legacy endpoint)."""
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


class ExportData(BaseModel):
    """Generic export result data."""
    postings_count: int = Field(..., description="Number of postings exported")
    transactions_count: int = Field(..., description="Number of transactions exported")
    duration_ms: int = Field(..., description="Export duration in milliseconds")
    path: str = Field(..., description="Path to database file")
    last_sync: str = Field(..., description="Last sync timestamp (ISO format)")
    db_type: str = Field(..., description="Database type")


class ExportStatusData(BaseModel):
    """Generic export status data."""
    db_type: str = Field(..., description="Database type")
    exists: bool = Field(..., description="Whether database file exists")
    path: str = Field(..., description="Path to database file")
    size_bytes: int = Field(..., description="File size in bytes")
    last_modified: Optional[str] = Field(None, description="Last modified timestamp (ISO format)")
    postings_count: int = Field(default=0, description="Number of postings in database")
    is_current: bool = Field(default=False, description="Whether database is up-to-date with ledger")
    ledger_modified: Optional[str] = Field(None, description="Ledger file modification timestamp")
