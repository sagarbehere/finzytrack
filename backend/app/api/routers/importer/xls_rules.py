import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from app.core.backup_manager import BackupManager
from app.core.config_manager import ConfigManager
from app.core.xls_rules_manager import XlsRulesManager
from app.dependencies import get_backup_manager, get_config_manager, get_xls_rules_manager
from app.exceptions import APIError
from app.helpers.rule_validation import parse_yaml, resolve_rule_path, validate_rule_schema
from app.schemas.xls_schemas import XlsRule, XlsRuleListData
from app.schemas.rule_write_schemas import (
    RuleContentResponse,
    RuleCreateRequest,
    RuleDeleteResponse,
    RuleWriteRequest,
    RuleWriteResponse,
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

# Keys that belong to CSV rules — reject if present in an XLS rule
_CSV_ONLY_KEYS = {"separator", "encoding"}

router = APIRouter()


def _xls_rules_dir(config_manager: ConfigManager) -> Path:
    return Path(config_manager.get_config().xls_rules_dir)


@router.get("/xls-rules", response_model=ApiResponse[XlsRuleListData], operation_id="listXlsRules")
async def list_xls_rules(
    xls_rules_manager: XlsRulesManager = Depends(get_xls_rules_manager),
):
    rules, invalid_rules = xls_rules_manager.list_rules()
    data = XlsRuleListData(
        rules=rules,
        invalid_rules=invalid_rules,
        rules_dir=xls_rules_manager.rules_dir,
    )
    return success_json_response(data)


@router.get("/xls-rules/{filename}", response_model=ApiResponse[XlsRule], operation_id="getXlsRule")
async def get_xls_rule(
    filename: str,
    xls_rules_manager: XlsRulesManager = Depends(get_xls_rules_manager),
):
    try:
        rule = xls_rules_manager.get_rule(filename)
    except FileNotFoundError:
        raise APIError(
            message=f"XLS rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )
    except ValueError as e:
        raise APIError(
            message=str(e),
            code=ec.VALIDATION_ERROR,
            status_code=400,
            details={"filename": filename},
        )
    except Exception as e:
        raise APIError(
            message=f"Failed to load XLS rule: {e}",
            code=ec.UNKNOWN_SERVER_ERROR,
            status_code=500,
            details={"filename": filename},
        )
    return success_json_response(rule)


@router.get(
    "/xls-rules/{filename}/raw",
    response_model=ApiResponse[RuleContentResponse],
    operation_id="getXlsRuleRaw",
)
async def get_xls_rule_raw(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Read raw YAML content of an XLS rule file."""
    rules_dir = _xls_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)
    if not target.exists():
        raise APIError(
            message=f"XLS rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )
    content = target.read_text(encoding="utf-8")
    return success_json_response(RuleContentResponse(filename=target.name, content=content))


@router.post(
    "/xls-rules",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="createXlsRule",
    status_code=201,
)
async def create_xls_rule(
    body: RuleCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Create a new XLS rule file."""
    rules_dir = _xls_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, body.filename)

    if target.exists():
        raise APIError(
            message=f"File '{target.name}' already exists. Use PUT to update it.",
            code=ec.RESOURCE_CONFLICT,
            status_code=409,
            details={"filename": target.name},
        )

    data = parse_yaml(body.content)
    csv_keys = _CSV_ONLY_KEYS & data.keys()
    if csv_keys:
        raise APIError(
            message=f"This rule contains CSV-specific fields ({', '.join(sorted(csv_keys))}) — use a CSV rule instead.",
            code=ec.VALIDATION_ERROR,
            status_code=400,
        )
    validate_rule_schema(data, XlsRule, "XLS")

    rules_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    logger.info(f"Created XLS rule: {target}")

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=False),
        status_code=201,
    )


@router.put(
    "/xls-rules/{filename}",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="updateXlsRule",
)
async def update_xls_rule(
    filename: str,
    body: RuleWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """Update an existing XLS rule file with atomic write + backup."""
    rules_dir = _xls_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"XLS rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )

    data = parse_yaml(body.content)
    csv_keys = _CSV_ONLY_KEYS & data.keys()
    if csv_keys:
        raise APIError(
            message=f"This rule contains CSV-specific fields ({', '.join(sorted(csv_keys))}) — use a CSV rule instead.",
            code=ec.VALIDATION_ERROR,
            status_code=400,
        )
    validate_rule_schema(data, XlsRule, "XLS")

    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.truncate()
        f.write(body.content)
    logger.info(f"Updated XLS rule: {target}")

    return success_json_response(
        RuleWriteResponse(filename=target.name, path=str(target), backup_created=True)
    )


@router.delete(
    "/xls-rules/{filename}",
    response_model=ApiResponse[RuleDeleteResponse],
    operation_id="deleteXlsRule",
)
async def delete_xls_rule(
    filename: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Delete an XLS rule file."""
    rules_dir = _xls_rules_dir(config_manager)
    target = resolve_rule_path(rules_dir, filename)

    if not target.exists():
        raise APIError(
            message=f"XLS rule file not found: {filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"filename": filename},
        )

    target.unlink()
    logger.info(f"Deleted XLS rule: {target}")

    return success_json_response(RuleDeleteResponse(filename=target.name))
