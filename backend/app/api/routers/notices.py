"""
Server notice channel router.

GET /api/notices — returns the current set of server-side advisories.
Folds in what used to be GET /api/ledger/errors and is the home for any
future non-fatal server-state advisory (e.g., multi-file syntax warnings,
background-job results).

See ``app/core/notice_service.py`` for the Notice model and the per-source
compute functions.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.notice_service import compute_all_notices
from app.core.config_manager import ConfigManager
from app.dependencies import get_sqlite_reader, get_config_manager
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.services.sqlite_reader import SqliteReader

router = APIRouter()


class NoticeModel(BaseModel):
    code: str = Field(..., description="Stable identifier for the notice kind.")
    severity: str = Field(..., description="error | warning | info")
    source: str = Field(..., description="ledger | system | background")
    title: str
    message: str
    signature: str = Field(
        ...,
        description=(
            "Discriminator that re-surfaces a previously-dismissed notice "
            "when it changes. Frontend dismissal state is keyed by (code, signature)."
        ),
    )
    dismissible: bool = True
    details: Optional[List[str]] = None
    learn_more_url: Optional[str] = Field(
        None,
        description="Optional URL to a docs page describing this notice in detail.",
    )


class NoticesResponse(BaseModel):
    notices: List[NoticeModel]


@router.get(
    "/notices",
    response_model=ApiResponse[NoticesResponse],
    operation_id="getNotices",
)
async def get_notices(
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Return the current server-side advisories.

    Stateless — recomputed on every call from the SQLite mirror and the
    on-disk ledger tree.
    """
    ledger_root = getattr(config_manager.config, "ledger_file", None)
    notices = compute_all_notices(sqlite_reader, ledger_root)
    return success_json_response(NoticesResponse(
        notices=[NoticeModel(**n.to_dict()) for n in notices],
    ))
