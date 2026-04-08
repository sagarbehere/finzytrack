import importlib.resources
import logging
from enum import Enum

from fastapi import APIRouter

from app.exceptions import APIError
from app.schemas.rule_write_schemas import RuleContentResponse
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


class RuleTemplateType(str, Enum):
    csv = "csv"
    xls = "xls"
    email = "email"


_TEMPLATE_FILES = {
    RuleTemplateType.csv: "csv-template.yaml",
    RuleTemplateType.xls: "xls-template.yaml",
    RuleTemplateType.email: "email-template.yaml",
}


@router.get(
    "/rule-templates/{rule_type}",
    response_model=ApiResponse[RuleContentResponse],
    operation_id="getRuleTemplate",
)
async def get_rule_template(rule_type: RuleTemplateType):
    """Return a YAML template for the given rule type."""
    template_filename = _TEMPLATE_FILES[rule_type]
    try:
        content = (
            importlib.resources.files("app.templates.rules")
            .joinpath(template_filename)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, AttributeError, ImportError) as e:
        logger.error(f"Failed to load rule template {template_filename}: {e}")
        raise APIError(
            message=f"Rule template not found: {template_filename}",
            code=ec.RESOURCE_NOT_FOUND,
            status_code=404,
        )
    return success_json_response(
        RuleContentResponse(filename=template_filename, content=content)
    )
