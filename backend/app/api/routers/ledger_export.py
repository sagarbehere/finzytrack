"""
Ledger export router - handles exporting ledger data to SQLite.
"""
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Body

from app.schemas.response_schemas import ApiResponse
from app.schemas.export_schemas import (
    ExportData,
    ExportStatusData,
)
from app.dependencies import get_beancount_manager, get_config_manager
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
):
    """
    Export ledger to SQLite database.

    Examples:
        POST /api/ledger/export
        POST /api/ledger/export {"force": true}
    """
    config = config_manager.get_config()
    exporter = SQLiteExporter(
        sqlite_path=config.analytics.sqlite.export_path,
        enable_wal=config.analytics.sqlite.enable_wal
    )

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

    entries = beancount_manager.cache.get_entries()
    result = await exporter.export_entries(entries, force=force)

    export_data = ExportData(**result, db_type="sqlite")
    return success_json_response(export_data)


@router.get(
    "/export/status",
    response_model=ApiResponse[ExportStatusData],
    operation_id="getExportStatus"
)
async def get_export_status(
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Get SQLite export status.

    Examples:
        GET /api/ledger/export/status
    """
    config = config_manager.get_config()
    exporter = SQLiteExporter(
        sqlite_path=config.analytics.sqlite.export_path,
        enable_wal=config.analytics.sqlite.enable_wal
    )

    status = await exporter.get_status()

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
        db_type="sqlite",
        exists=status["exists"],
        path=status["path"],
        size_bytes=status["size_bytes"],
        last_modified=status["last_modified"],
        postings_count=status["postings_count"],
        is_current=is_current,
        ledger_modified=ledger_modified
    )

    return success_json_response(status_data)
