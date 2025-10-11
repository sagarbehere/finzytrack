"""
Ledger export router - handles exporting ledger data to various formats.
"""
import os
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from app.schemas.response_schemas import ApiResponse
from app.schemas.export_schemas import (
    DuckDBExportRequest,
    DuckDBExportData,
    DuckDBStatusData
)
from app.dependencies import get_beancount_manager, get_config_manager
from app.services.duckdb_exporter import DuckDBExporter
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


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
