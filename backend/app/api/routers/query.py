"""
Query router - handles executing queries on the ledger.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Body

from app.schemas.response_schemas import ApiResponse
from app.schemas.query_schemas import QueryRequest, QueryData
from app.schemas.export_schemas import DatabaseType
from app.dependencies import get_beancount_manager, get_config_manager
from app.services.duckdb_exporter import DuckDBExporter
from app.services.sqlite_exporter import SQLiteExporter
from app.services.beanquery_service import BeanqueryService
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/query",
    response_model=ApiResponse[QueryData],
    operation_id="executeQuery"
)
async def execute_query(
    request: QueryRequest = Body(...),
    db_type: Optional[str] = Query(
        None,
        description="Database/engine type: 'duckdb', 'sqlite', or 'beanquery'. If not specified, uses active database from config."
    ),
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Execute a query against the specified database engine.

    Examples:
        POST /api/ledger/query {"query": "SELECT * FROM postings LIMIT 10"}
        POST /api/ledger/query?db_type=sqlite {"query": "SELECT account, SUM(amount) FROM postings GROUP BY account"}
        POST /api/ledger/query?db_type=beanquery {"query": "SELECT account, sum(position) FROM postings GROUP BY account"}
    """
    config = config_manager.get_config()
    
    # Determine the query engine
    if db_type:
        engine = db_type.lower()
        if engine not in ["duckdb", "sqlite", "beanquery"]:
            raise APIError(
                message="Invalid database/engine type",
                code="ENGINE_NOT_SUPPORTED",
                status_code=400,
                details={"supported_engines": ["duckdb", "sqlite", "beanquery"]}
            )
    else:
        # Default to active database if not specified
        engine = config.analytics.metabase.db_type.value
    
    # Check for ledger errors
    errors = beancount_manager.cache.get_errors()
    if errors:
        raise APIError(
            message="Ledger file has parsing errors",
            code="LEDGER_PARSE_ERROR",
            status_code=400,
            details={
                "error_count": len(errors),
                "errors": [str(e) for e in errors[:5]]
            }
        )
    
    # Execute query based on engine
    if engine == "beanquery":
        # Use beanquery service
        beanquery_service = BeanqueryService()
        entries = beancount_manager.cache.get_entries()
        options = beancount_manager.cache.get_cached_data().options
        
        try:
            result = await asyncio.wait_for(
                beanquery_service.execute_query(entries, options, request.query),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise APIError(
                message="Query execution timed out",
                code="QUERY_TIMEOUT",
                status_code=408
            )
        except Exception as e:
            # Parse beanquery errors for better error messages
            error_msg = str(e)
            if "syntax error" in error_msg.lower():
                raise APIError(
                    message="Beanquery syntax error",
                    code="QUERY_SYNTAX_ERROR",
                    status_code=400,
                    details={"error": error_msg}
                )
            else:
                raise APIError(
                    message="Failed to execute beanquery",
                    code="QUERY_EXECUTION_ERROR",
                    status_code=500,
                    details={"error": error_msg}
                )
    
    else:
        # Use SQL database (DuckDB or SQLite)
        if engine == DatabaseType.DUCKDB.value:
            exporter = DuckDBExporter(duckdb_path=config.analytics.duckdb.export_path)
        else:  # DatabaseType.SQLITE
            exporter = SQLiteExporter(sqlite_path=config.analytics.sqlite.export_path)
        
        # Check if database exists
        status = await exporter.get_status()
        if not status["exists"]:
            raise APIError(
                message=f"Database {engine} does not exist. Please export the ledger first.",
                code="DATABASE_NOT_FOUND",
                status_code=404,
                details={"db_type": engine, "path": exporter.export_path}
            )
        
        # Execute SQL query
        try:
            result = await asyncio.wait_for(
                _execute_sql_query(exporter, request.query),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise APIError(
                message="Query execution timed out",
                code="QUERY_TIMEOUT",
                status_code=408
            )
        except Exception as e:
            # Parse SQL errors for better error messages
            error_msg = str(e)
            if "syntax error" in error_msg.lower() or "parse error" in error_msg.lower():
                raise APIError(
                    message="SQL syntax error",
                    code="QUERY_SYNTAX_ERROR",
                    status_code=400,
                    details={"error": error_msg}
                )
            elif "no such table" in error_msg.lower():
                raise APIError(
                    message="Table not found",
                    code="TABLE_NOT_FOUND",
                    status_code=400,
                    details={"error": error_msg}
                )
            else:
                raise APIError(
                    message="Failed to execute SQL query",
                    code="QUERY_EXECUTION_ERROR",
                    status_code=500,
                    details={"error": error_msg}
                )
    
    # Build response
    query_data = QueryData(
        query=request.query,
        engine=engine,
        execution_time_ms=result["execution_time_ms"],
        row_count=result["row_count"],
        columns=result["columns"],
        rows=result["rows"]
    )
    
    return success_json_response(query_data)


async def _execute_sql_query(exporter, query_str: str) -> dict:
    """
    Execute SQL query on DuckDB or SQLite database.
    
    This is a helper function that abstracts the SQL execution logic.
    """
    import time
    from pathlib import Path
    
    start_time = time.time()
    
    # Determine database type and execute query accordingly
    if hasattr(exporter, 'duckdb_path'):  # DuckDB
        import duckdb
        
        # Run blocking DuckDB operations in thread pool
        result = await asyncio.to_thread(
            _execute_duckdb_query,
            exporter.duckdb_path,
            query_str
        )
    else:  # SQLite
        import sqlite3
        
        # Run blocking SQLite operations in thread pool
        result = await asyncio.to_thread(
            _execute_sqlite_query,
            exporter.export_path,
            query_str
        )
    
    execution_time_ms = int((time.time() - start_time) * 1000)
    result["execution_time_ms"] = execution_time_ms
    
    return result


def _execute_duckdb_query(db_path: str, query_str: str) -> dict:
    """Execute DuckDB query (blocking I/O)."""
    import duckdb
    
    # DuckDB doesn't have a read_only pragma like SQLite
    # We'll connect normally and rely on not using write operations
    con = duckdb.connect(db_path, read_only=True)
    
    try:
        # Execute query and get results
        result = con.execute(query_str)
        
        # Get column information
        columns = []
        for desc in result.description:
            columns.append({
                "name": desc[0],
                "type": str(desc[1]) if desc[1] else "UNKNOWN"
            })
        
        # Fetch all rows
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        row_dicts = []
        for row in rows:
            row_dict = {}
            for i, value in enumerate(row):
                column_name = columns[i]["name"] if i < len(columns) else f"col_{i}"
                # Convert DuckDB types to JSON-serializable format
                if hasattr(value, '__dict__'):  # DuckDB objects
                    row_dict[column_name] = str(value)
                else:
                    row_dict[column_name] = value
            row_dicts.append(row_dict)
        
        return {
            "success": True,
            "row_count": len(row_dicts),
            "columns": columns,
            "rows": row_dicts
        }
    
    finally:
        con.close()


def _execute_sqlite_query(db_path: str, query_str: str) -> dict:
    """Execute SQLite query (blocking I/O)."""
    import sqlite3
    
    con = sqlite3.connect(db_path, uri=True)
    # Set read-only mode using URI
    con.execute('PRAGMA query_only = true')
    con.row_factory = sqlite3.Row  # Enable row factory for column access
    
    try:
        cursor = con.execute(query_str)
        
        # Get column information
        columns = []
        if cursor.description:
            for desc in cursor.description:
                columns.append({
                    "name": desc[0],
                    "type": "TEXT"  # SQLite doesn't have rich type info in cursor.description
                })
        
        # Fetch all rows
        rows = cursor.fetchall()
        
        # Convert rows to list of dictionaries
        row_dicts = []
        for row in rows:
            row_dict = {}
            for i, value in enumerate(row):
                column_name = columns[i]["name"] if i < len(columns) else f"col_{i}"
                row_dict[column_name] = value
            row_dicts.append(row_dict)
        
        return {
            "success": True,
            "row_count": len(row_dicts),
            "columns": columns,
            "rows": row_dicts
        }
    
    finally:
        con.close()