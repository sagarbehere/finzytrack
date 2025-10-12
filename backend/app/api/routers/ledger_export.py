"""
Ledger export router - handles exporting ledger data to various formats.
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Body

from app.schemas.response_schemas import ApiResponse
from app.schemas.export_schemas import (
    DuckDBExportRequest,
    DuckDBExportData,
    DuckDBStatusData,
    ExportRequest,
    ExportData,
    ExportStatusData,
    DatabaseType
)
from app.dependencies import get_beancount_manager, get_config_manager
from app.services.duckdb_exporter import DuckDBExporter
from app.services.sqlite_exporter import SQLiteExporter
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/export",
    response_model=ApiResponse[ExportData],
    operation_id="exportLedger"
)
async def export_ledger(
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    force: bool = Body(default=False, embed=True),
    db_type: Optional[DatabaseType] = Body(default=None, embed=True)
):
    """
    Export ledger to specified database or active database.

    This endpoint allows manual export to either DuckDB or SQLite, regardless of
    which database is configured as active in analytics.metabase.db_type.

    Examples:
        POST /api/ledger/export                           # Export to active database (from config)
        POST /api/ledger/export {"db_type": "sqlite"}    # Explicitly export to SQLite
        POST /api/ledger/export {"db_type": "duckdb"}    # Explicitly export to DuckDB
    """
    config = config_manager.get_config()

    # Default to active database if not specified (Pydantic validates enum)
    target_db = db_type.value if db_type else config.analytics.metabase.db_type.value

    # Get the appropriate exporter
    if target_db == DatabaseType.DUCKDB.value:
        exporter = DuckDBExporter(duckdb_path=config.analytics.duckdb.export_path)
    else:  # DatabaseType.SQLITE
        exporter = SQLiteExporter(sqlite_path=config.analytics.sqlite.export_path)

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

    # Export to target database
    entries = beancount_manager.cache.get_entries()
    result = await exporter.export_entries(entries, force=force)

    # Add db_type to result
    export_data = ExportData(**result, db_type=target_db)
    return success_json_response(export_data)


@router.get(
    "/export/status",
    response_model=ApiResponse[ExportStatusData],
    operation_id="getExportStatus"
)
async def get_export_status(
    db_type: Optional[DatabaseType] = Query(
        None,
        description="Database type. If not specified, uses active database from config."
    ),
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Get export status for specified database or active database.

    Examples:
        GET /api/ledger/export/status                    # Status of active database
        GET /api/ledger/export/status?db_type=sqlite     # Status of SQLite database
        GET /api/ledger/export/status?db_type=duckdb     # Status of DuckDB database
    """
    config = config_manager.get_config()

    # Default to active database if not specified (Pydantic validates enum)
    target_db = db_type.value if db_type else config.analytics.metabase.db_type.value

    # Get the appropriate exporter
    if target_db == DatabaseType.DUCKDB.value:
        exporter = DuckDBExporter(duckdb_path=config.analytics.duckdb.export_path)
    else:  # DatabaseType.SQLITE
        exporter = SQLiteExporter(sqlite_path=config.analytics.sqlite.export_path)

    # Get status
    status = await exporter.get_status()

    # Check if database is current compared to ledger
    ledger_file = Path(beancount_manager.ledger_file)
    is_current = False
    ledger_modified = None

    if ledger_file.exists() and status["exists"]:
        ledger_stat = ledger_file.stat()
        ledger_modified = datetime.fromtimestamp(ledger_stat.st_mtime).isoformat()

        if status["last_modified"]:
            db_mtime = datetime.fromisoformat(status["last_modified"])
            ledger_mtime = datetime.fromtimestamp(ledger_stat.st_mtime)
            is_current = db_mtime >= ledger_mtime

    status_data = ExportStatusData(
        db_type=target_db,
        exists=status["exists"],
        path=status["path"],
        size_bytes=status["size_bytes"],
        last_modified=status["last_modified"],
        postings_count=status["postings_count"],
        is_current=is_current,
        ledger_modified=ledger_modified
    )

    return success_json_response(status_data)


def get_duckdb_exporter(
    config_manager: ConfigManager = Depends(get_config_manager)
) -> DuckDBExporter:
    """Dependency to get DuckDB exporter."""
    config = config_manager.get_config()
    return DuckDBExporter(duckdb_path=config.analytics.duckdb.export_path)


@router.post(
    "/export/duckdb",
    response_model=ApiResponse[DuckDBExportData],
    operation_id="exportToDuckDB"
)
async def export_to_duckdb(
    request: DuckDBExportRequest = DuckDBExportRequest(),
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    duckdb_exporter: DuckDBExporter = Depends(get_duckdb_exporter)
):
    """
    Export current ledger to DuckDB format.

    This endpoint manually triggers a DuckDB export. Normally, exports happen
    automatically when the ledger changes (with debouncing), but this endpoint
    allows immediate manual export.
    """
    # Check if ledger has errors
    errors = beancount_manager.cache.get_errors()
    if errors:
        raise APIError(
            message="Ledger file has parsing errors",
            code="LEDGER_PARSE_ERROR",
            status_code=400,
            details={
                "error_count": len(errors),
                "errors": [str(e) for e in errors[:5]]  # First 5 errors
            }
        )

    # Get entries from cache
    entries = beancount_manager.cache.get_entries()

    # Export to DuckDB
    result = await duckdb_exporter.export_entries(entries, force=request.force)

    export_data = DuckDBExportData(**result)
    return success_json_response(export_data)


@router.get(
    "/export/duckdb/status",
    response_model=ApiResponse[DuckDBStatusData],
    operation_id="getDuckDBStatus"
)
async def get_duckdb_status(
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    duckdb_exporter: DuckDBExporter = Depends(get_duckdb_exporter),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Get DuckDB export status.

    Returns information about the DuckDB export file, including whether it exists,
    its size, last modification time, and whether it's up-to-date with the ledger.
    """
    # Get status from exporter
    status = await duckdb_exporter.get_status()

    # Check if DuckDB is current compared to ledger
    config = config_manager.get_config()
    ledger_file = Path(beancount_manager.ledger_file)

    is_current = False
    ledger_modified = None

    if ledger_file.exists() and status["exists"]:
        ledger_stat = ledger_file.stat()
        ledger_modified = datetime.fromtimestamp(ledger_stat.st_mtime).isoformat()

        # Compare modification times
        if status["last_modified"]:
            duckdb_mtime = datetime.fromisoformat(status["last_modified"])
            ledger_mtime = datetime.fromtimestamp(ledger_stat.st_mtime)
            is_current = duckdb_mtime >= ledger_mtime

    status_data = DuckDBStatusData(
        exists=status["exists"],
        path=status["path"],
        size_bytes=status["size_bytes"],
        last_modified=status["last_modified"],
        postings_count=status["postings_count"],
        is_current=is_current,
        ledger_modified=ledger_modified
    )

    return success_json_response(status_data)
