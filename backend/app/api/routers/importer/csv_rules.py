import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from app.core.backup_manager import BackupManager
from app.core.config_manager import ConfigManager
from app.core.csv_rules_manager import CsvRulesManager
from app.dependencies import get_backup_manager, get_config_manager, get_csv_rules_manager
from app.exceptions import APIError
from app.helpers.rule_validation import parse_yaml, resolve_rule_path, validate_rule_schema
from app.schemas.csv_schemas import CsvRule, CsvRuleListData
from app.schemas.rule_write_schemas import (
    RuleContentResponse,
    RuleCreateRequest,
    RuleDeleteResponse,
    RuleWriteRequest,
    RuleWriteResponse,
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

# Keys that belong to XLS rules — reject if present in a CSV rule
_XLS_ONLY_KEYS = {"sheet_index", "sheet_name"}

router = APIRouter()


def _csv_rules_dir(config_manager: ConfigManager) -> Path:
    return Path(config_manager.get_config().csv_rules_dir)


@router.get("/csv-rules", response_model=ApiResponse[CsvRuleListData], operation_id="listCsvRules")
async def list_csv_rules(
    csv_rules_manager: CsvRulesManager = Depends(get_csv_rules_manager),
):
    rules, invalid_rules = csv_rules_manager.list_rules()
    data = CsvRuleListData(
        rules=rules,
        invalid_rules=invalid_rules,
        rules_dir=csv_rules_manager.rules_dir,
    )
    return success_json_response(data)


@router.get("/csv-rules/{filename}", response_model=ApiResponse[CsvRule], operation_id="getCsvRule")
async def get_csv_rule(
    filename: str,
    csv_rules_manager: CsvRulesManager = Depends(get_csv_rules_manager),
):
    try:
        rule = csv_rules_manager.get_rule(filename)
    except FileNotFoundError:
        raise APIError(
            message=f"CSV rule file not found: {filename}",
            code="RESOURCE_NOT_FOUND",
            status_code=404,
            details={"filename": filename},
        )
    except ValueError as e:
        raise APIError(
            message=str(e),
            code="VALIDATION_ERROR",
            status_code=400,
            details={"filename": filename},
        )
    except Exception as e:
        raise APIError(
            message=f"Failed to load CSV rule: {e}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500,
            details={"filename": filename},
        )
    return success_json_response(rule)


@router.get(
    "/csv-rules/{filename}/raw",
    response_model=ApiResponse[RuleContentResponse],
    operation_id="getCsvRuleRaw",
)
async def get_csv_rule_raw(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Read raw YAML content of a CSV rule file."""
    rules_dir = _csv_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)
    if not target.exists():
        raise APIError(
            message=f"CSV rule file not found: {filename}",
            code="RESOURCE_NOT_FOUND",
            status_code=404,
            details={"filename": filename},
        )
    content = target.read_text(encoding="utf-8")
    return success_json_response(RuleContentResponse(filename=target.name, content=content))


@router.post(
    "/csv-rules",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="createCsvRule",
    status_code=201,
)
async def create_csv_rule(
    body: RuleCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Create a new CSV rule file."""
    rules_dir = _csv_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, body.filename)

    if target.exists():
        raise APIError(
            message=f"File '{target.name}' already exists. Use PUT to update it.",
            code="RESOURCE_CONFLICT",
            status_code=409,
            details={"filename": target.name},
        )

    data = parse_yaml(body.content)
    xls_keys = _XLS_ONLY_KEYS & data.keys()
    if xls_keys:
        raise APIError(
            message=f"This rule contains XLS-specific fields ({', '.join(sorted(xls_keys))}) — use an XLS rule instead.",
            code="VALIDATION_ERROR",
            status_code=400,
        )
    validate_rule_schema(data, CsvRule, "CSV")

    rules_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    logger.info(f"Created CSV rule: {target}")

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=False),
        status_code=201,
    )


@router.put(
    "/csv-rules/{filename}",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="updateCsvRule",
)
async def update_csv_rule(
    filename: str,
    body: RuleWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """Update an existing CSV rule file with atomic write + backup."""
    rules_dir = _csv_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"CSV rule file not found: {filename}",
            code="RESOURCE_NOT_FOUND",
            status_code=404,
            details={"filename": filename},
        )

    data = parse_yaml(body.content)
    xls_keys = _XLS_ONLY_KEYS & data.keys()
    if xls_keys:
        raise APIError(
            message=f"This rule contains XLS-specific fields ({', '.join(sorted(xls_keys))}) — use an XLS rule instead.",
            code="VALIDATION_ERROR",
            status_code=400,
        )
    validate_rule_schema(data, CsvRule, "CSV")

    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write(body.content)
    logger.info(f"Updated CSV rule: {target}")

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=True)
    )


@router.delete(
    "/csv-rules/{filename}",
    response_model=ApiResponse[RuleDeleteResponse],
    operation_id="deleteCsvRule",
)
async def delete_csv_rule(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Delete a CSV rule file."""
    rules_dir = _csv_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"CSV rule file not found: {filename}",
            code="RESOURCE_NOT_FOUND",
            status_code=404,
            details={"filename": filename},
        )

    target.unlink()
    logger.info(f"Deleted CSV rule: {target}")

    return success_json_response(RuleDeleteResponse(filename=target.name))
