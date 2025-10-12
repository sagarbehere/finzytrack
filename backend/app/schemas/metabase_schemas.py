"""
Pydantic schemas for Metabase operations.
"""
from typing import Optional
from pydantic import BaseModel, Field


class MetabaseStatusData(BaseModel):
    """Metabase status data."""
    running: bool = Field(..., description="Whether Metabase is running")
    healthy: bool = Field(..., description="Whether Metabase is responding")
    initialized: bool = Field(..., description="Whether Metabase has been initialized")
    port: int = Field(..., description="Metabase port")
    uptime_seconds: int = Field(..., description="Uptime in seconds")
    pid: Optional[int] = Field(None, description="Process ID")
    started_at: Optional[str] = Field(None, description="Start time (ISO format)")


class MetabaseStartData(BaseModel):
    """Metabase start result data."""
    pid: int = Field(..., description="Process ID")
    port: int = Field(..., description="Metabase port")
    url: str = Field(..., description="Metabase URL")
    started_at: str = Field(..., description="Start time (ISO format)")


class MetabaseStopData(BaseModel):
    """Metabase stop result data."""
    stopped_at: str = Field(..., description="Stop time (ISO format)")
    uptime_seconds: int = Field(..., description="Uptime in seconds before stopping")


class MetabaseInitializeData(BaseModel):
    """Metabase initialization result data."""
    admin_email: str = Field(..., description="Admin email")
    admin_password: str = Field(..., description="Admin password (one-time display)")
    session_token: str = Field(..., description="Session token")
    database_id: int = Field(..., description="DuckDB database ID in Metabase")
    dashboards_imported: int = Field(..., description="Number of dashboards imported")


class MetabaseResetData(BaseModel):
    """Metabase reset result data."""
    reset_successful: bool = Field(..., description="Whether the reset was successful")
    timestamp: str = Field(..., description="Timestamp of the reset (ISO format)")


class MetabaseSyncSchemaData(BaseModel):
    """Metabase schema sync result data."""
    synced_at: str = Field(..., description="Sync time (ISO format)")
    database_id: int = Field(..., description="Database ID that was synced")
