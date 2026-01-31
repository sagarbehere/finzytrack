from fastapi import APIRouter, Depends

from app.core.csv_rules_manager import CsvRulesManager
from app.dependencies import get_csv_rules_manager
from app.exceptions import APIError
from app.schemas.csv_schemas import CsvRule, CsvRuleListData
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

router = APIRouter()


@router.get("/csv-rules", response_model=ApiResponse[CsvRuleListData], operation_id="listCsvRules")
async def list_csv_rules(
    csv_rules_manager: CsvRulesManager = Depends(get_csv_rules_manager),
):
    rules = csv_rules_manager.list_rules()
    data = CsvRuleListData(
        rules=rules,
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
