"""
Query router - handles executing queries on the ledger.
"""
import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Body
from fastapi.responses import PlainTextResponse

from app.schemas.response_schemas import ApiResponse
from app.schemas.query_schemas import QueryRequest, QueryData
from app.dependencies import get_config_manager, get_sqlite_reader
from app.services.sqlite_exporter import SQLiteExporter
from app.services.beanquery_service import BeanqueryService
from app.services.sqlite_reader import SqliteReader
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

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
        description="Database/engine type: 'sqlite' or 'beanquery'. Defaults to 'sqlite'."
    ),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """
    Execute a query against the specified database engine.

    Examples:
        POST /api/ledger/query {"query": "SELECT * FROM postings LIMIT 10"}
        POST /api/ledger/query?db_type=sqlite {"query": "SELECT account, SUM(amount) FROM postings GROUP BY account"}
        POST /api/ledger/query?db_type=beanquery {"query": "SELECT account, sum(position) FROM postings GROUP BY account"}
    """
    engine = (db_type or "sqlite").lower()

    if engine not in ["sqlite", "beanquery"]:
        raise APIError(
            message="Invalid database/engine type",
            code=ec.ENGINE_NOT_SUPPORTED,
            status_code=400,
            details={"supported_engines": ["sqlite", "beanquery"]}
        )

    # Check for ledger errors (filter out warnings like PadError which are non-fatal)
    raw_errors = sqlite_reader.get_errors()
    # Filter non-fatal errors by message content (class info not available in SQLite)
    fatal_errors = [
        e for e in raw_errors
        if "pad" not in (e.get("message", "") or "").lower()
    ]

    if fatal_errors:
        raise APIError(
            message="Ledger file has parsing errors",
            code=ec.LEDGER_PARSE_ERROR,
            status_code=400,
            details={
                "error_count": len(fatal_errors),
                "errors": [e.get("message", "") for e in fatal_errors[:5]]
            }
        )

    if engine == "beanquery":
        # BQL requires full entries in memory — transient parse
        from app.core.ledger_loader import load_ledger_checked
        beanquery_service = BeanqueryService()
        config = config_manager.get_config()
        entries, _, options = load_ledger_checked(config.ledger_file)

        try:
            result = await asyncio.wait_for(
                beanquery_service.execute_query(entries, options, request.query),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise APIError(
                message="Query execution timed out",
                code=ec.QUERY_TIMEOUT,
                status_code=408
            )
        except Exception as e:
            error_msg = str(e)
            if "syntax error" in error_msg.lower():
                raise APIError(
                    message="Beanquery syntax error",
                    code=ec.QUERY_SYNTAX_ERROR,
                    status_code=400,
                    details={"error": error_msg}
                )
            else:
                raise APIError(
                    message="Failed to execute beanquery",
                    code=ec.QUERY_EXECUTION_ERROR,
                    status_code=500,
                    details={"error": error_msg}
                )

    else:  # sqlite
        config = config_manager.get_config()
        exporter = SQLiteExporter(sqlite_path=config.sqlite_export_path)

        status = await exporter.get_status()
        if not status["exists"]:
            raise APIError(
                message="SQLite database does not exist. Please export the ledger first.",
                code=ec.DATABASE_NOT_FOUND,
                status_code=404,
                details={"db_type": "sqlite", "path": exporter.export_path}
            )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_execute_sqlite_query, exporter.export_path, request.query),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            raise APIError(
                message="Query execution timed out",
                code=ec.QUERY_TIMEOUT,
                status_code=408
            )
        except Exception as e:
            error_msg = str(e)
            if "syntax error" in error_msg.lower() or "parse error" in error_msg.lower():
                raise APIError(
                    message="SQL syntax error",
                    code=ec.QUERY_SYNTAX_ERROR,
                    status_code=400,
                    details={"error": error_msg}
                )
            elif "no such table" in error_msg.lower():
                raise APIError(
                    message="Table not found",
                    code=ec.TABLE_NOT_FOUND,
                    status_code=400,
                    details={"error": error_msg}
                )
            else:
                raise APIError(
                    message="Failed to execute SQL query",
                    code=ec.QUERY_EXECUTION_ERROR,
                    status_code=500,
                    details={"error": error_msg}
                )

    query_data = QueryData(
        query=request.query,
        engine=engine,
        execution_time_ms=result["execution_time_ms"],
        row_count=result["row_count"],
        columns=result["columns"],
        rows=result["rows"]
    )

    return success_json_response(query_data)


_SCHEMA_POSTINGS_PATH = Path(__file__).parents[3] / "resources" / "prompts" / "schema_postings.md"


@router.get(
    "/schema/postings",
    response_class=PlainTextResponse,
    operation_id="getPostingsSchema",
)
async def get_postings_schema():
    """Return the postings table schema as Markdown (used by the frontend SQL assistant)."""
    if not _SCHEMA_POSTINGS_PATH.is_file():
        raise APIError(
            message="Schema file not found",
            code=ec.SCHEMA_NOT_FOUND,
            status_code=500,
        )
    return PlainTextResponse(
        content=_SCHEMA_POSTINGS_PATH.read_text(encoding="utf-8"),
        media_type="text/markdown",
    )


def _execute_sqlite_query(db_path: str, query_str: str) -> dict:
    """Execute SQLite query (blocking I/O)."""
    start_time = time.time()

    con = sqlite3.connect(db_path, uri=True)
    con.execute('PRAGMA query_only = true')
    con.row_factory = sqlite3.Row

    try:
        cursor = con.execute(query_str)

        columns = []
        if cursor.description:
            for desc in cursor.description:
                columns.append({
                    "name": desc[0],
                    "type": "TEXT"
                })

        rows = cursor.fetchall()

        row_dicts = []
        for row in rows:
            row_dict = {}
            for i, value in enumerate(row):
                column_name = columns[i]["name"] if i < len(columns) else f"col_{i}"
                row_dict[column_name] = value
            row_dicts.append(row_dict)

        return {
            "success": True,
            "execution_time_ms": int((time.time() - start_time) * 1000),
            "row_count": len(row_dicts),
            "columns": columns,
            "rows": row_dicts
        }

    finally:
        con.close()
