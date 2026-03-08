"""
Pydantic schemas for query operations.
"""
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request to execute a query."""
    query: str = Field(..., description="SQL or beanquery to execute")


class QueryColumnInfo(BaseModel):
    """Column information for query results."""
    name: str = Field(..., description="Column name")
    type: str = Field(..., description="Column type")


class QueryData(BaseModel):
    """Query result data."""
    query: str = Field(..., description="The executed query")
    engine: str = Field(..., description="Query engine used (sqlite or beanquery)")
    execution_time_ms: int = Field(..., description="Query execution time in milliseconds")
    row_count: int = Field(..., description="Number of rows returned")
    columns: List[QueryColumnInfo] = Field(..., description="Column information")
    rows: List[Dict[str, Any]] = Field(..., description="Query result rows")