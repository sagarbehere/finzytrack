from fastapi import APIRouter, Depends

from app.core.xls_rules_manager import XlsRulesManager
from app.dependencies import get_xls_rules_manager
from app.exceptions import APIError
from app.schemas.xls_schemas import XlsRule, XlsRuleListData
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

router = APIRouter()


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
            message=f"Failed to load XLS rule: {e}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500,
            details={"filename": filename},
        )
    return success_json_response(rule)
