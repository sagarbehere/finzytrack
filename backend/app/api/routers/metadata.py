"""Metadata router — the recognized vocabulary and the metadata review that back
Investments → Setup & Metadata (dev-docs/investment-view.md §10.3). Read-only;
commodity metadata is written via `PUT /api/commodities/metadata`, account
metadata via `PUT /api/accounts/{name}`.
"""

import logging

from fastapi import APIRouter, Depends

from app.core.config_manager import ConfigManager
from app.core.metadata_vocabulary import CURRENCY_ASSET_CLASSES, FIELDS
from app.dependencies import get_config_manager, get_sqlite_reader
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response
from app.schemas.metadata_schemas import (
    MetadataFieldSpec,
    MetadataReviewData,
    MetadataVocabularyData,
)
from app.schemas.response_schemas import ApiResponse
from app.services.metadata_review import build_review
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/metadata/vocabulary",
    response_model=ApiResponse[MetadataVocabularyData],
    operation_id="getMetadataVocabulary",
)
async def get_metadata_vocabulary():
    """The recognized metadata keys and values. Recognition only — the ledger may
    carry any key and any value."""
    fields = [
        MetadataFieldSpec(
            key=f.key, scope=f.scope, kind=f.kind, label=f.label, adds=f.adds,
            values=list(f.values), review=f.review,
            requires_key=f.requires_key, requires_values=list(f.requires_values),
        )
        for f in FIELDS
    ]
    return success_json_response(MetadataVocabularyData(
        fields=fields,
        currency_asset_classes=sorted(CURRENCY_ASSET_CLASSES),
    ))


@router.get(
    "/metadata/review",
    response_model=ApiResponse[MetadataReviewData],
    operation_id="getMetadataReview",
)
async def get_metadata_review(
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Commodities and accounts the vocabulary can describe, with their current
    metadata and the relevant keys that are not set."""
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        return success_json_response(build_review(sqlite_reader))
