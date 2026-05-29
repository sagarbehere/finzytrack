import io
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from ruamel.yaml import YAML

from app.config import OFXAccountMapping
from app.core.backup_manager import BackupManager
from app.core.ledger_manager import LedgerManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.dependencies import get_backup_manager, get_config_manager, get_beancount_manager, get_sqlite_reader
from app.services.sqlite_reader import SqliteReader
from app.schemas.ofx_schemas import (
    OFXDetectionRequest,
    OFXDetectionData,
    LearnOFXAccountRequest,
    LearnOFXAccountData
)
from app.schemas.rule_write_schemas import (
    RuleContentResponse,
    RuleWriteRequest,
    RuleWriteResponse,
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/detect-ofx-account", response_model=ApiResponse[OFXDetectionData], operation_id="detectOfxAccount")
async def detect_ofx_account(
    request: OFXDetectionRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    config = config_manager.get_config()
    try:
        detailed_accounts = sqlite_reader.get_accounts()
        if not detailed_accounts:
            # This is a specific case where the ledger exists but is empty or uninitialized.
            raise APIError(
                message="Ledger file contains no open accounts.",
                code=ec.RESOURCE_NOT_FOUND,
                status_code=404,
                details={"resource_type": "account", "ledger_file": config.ledger_file}
            )
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code=ec.FILE_NOT_FOUND, status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code=ec.FILE_PERMISSION_ERROR, status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to read ledger file: {e}", code=ec.UNKNOWN_SERVER_ERROR, status_code=500, details={"path": config.ledger_file})
    
    for mapping in config_manager.get_ofx_mappings():
        if (mapping.institution.lower() == request.institution.lower() and
            (mapping.institution_fid or "").lower() == (request.institution_fid or "").lower() and
            mapping.account_type.lower() == request.account_type.lower() and
            mapping.account_id == request.account_id):
            
            detection_data = OFXDetectionData(
                detected=True,
                beancount_account=mapping.beancount_account,
                currency=mapping.currency,
                message=f"Account detected successfully: {mapping.beancount_account}"
            )
            return success_json_response(detection_data)
    
    detection_data = OFXDetectionData(
        detected=False,
        beancount_account=config.accounts.default_unknown_account,
        currency=config.accounts.default_currency,
        message="No matching account configuration found. Please verify account details."
    )
    return success_json_response(detection_data)

@router.post("/learn-ofx-account", response_model=ApiResponse[LearnOFXAccountData], operation_id="learnOfxAccount")
async def learn_ofx_account(
    request: LearnOFXAccountRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    config = config_manager.get_config()
    if not beancount_manager.validate_account_format(request.beancount_account):
        raise APIError(
            message="Invalid account format",
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={
                "field": "beancount_account",
                "account_name": request.beancount_account,
                "help": "Account name must follow Beancount naming conventions"
            }
        )

    try:
        account_exists = request.beancount_account in sqlite_reader.get_account_names()
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code=ec.FILE_NOT_FOUND, status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code=ec.FILE_PERMISSION_ERROR, status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to validate account: {e}", code=ec.UNKNOWN_SERVER_ERROR, status_code=500, details={"path": config.ledger_file})
    
    if not account_exists:
        learn_data = LearnOFXAccountData(
            mapping_saved=False,
            account_creation_needed=True
        )
        return success_json_response(learn_data)
    
    new_mapping = OFXAccountMapping(
        institution=request.institution,
        institution_fid=request.institution_fid,
        account_type=request.account_type,
        account_id=request.account_id,
        currency=request.currency,
        beancount_account=request.beancount_account
    )
    
    for existing_mapping in config_manager.get_ofx_mappings():
        if (existing_mapping.institution.lower() == request.institution.lower() and
            (existing_mapping.institution_fid or "").lower() == (request.institution_fid or "").lower() and
            existing_mapping.account_type.lower() == request.account_type.lower() and
            existing_mapping.account_id == request.account_id):
            
            raise APIError(
                message=f"Mapping already exists for this OFX account: {existing_mapping.beancount_account}",
                code=ec.RESOURCE_CONFLICT,
                status_code=409
            )

    try:
        config_manager.add_ofx_mapping(new_mapping)
        learn_data = LearnOFXAccountData(
            mapping_saved=True,
            account_creation_needed=False
        )
        return success_json_response(learn_data)
        
    except PermissionError:
        raise APIError(
            message="Permission denied saving configuration file",
            code=ec.CONFIG_SAVE_ERROR,
            status_code=403,
            details={"path": getattr(config, 'config_path', 'config.yaml')}
        )
    except Exception as e:
        raise APIError(
            message="Failed to save mapping to configuration file",
            code=ec.UNKNOWN_SERVER_ERROR,
            status_code=500,
            details={"error": str(e)}
        )


# ---------------------------------------------------------------------------
# OFX mappings raw read / write
# ---------------------------------------------------------------------------

@router.get(
    "/ofx-mappings/raw",
    response_model=ApiResponse[RuleContentResponse],
    operation_id="getOfxMappingsRaw",
)
async def get_ofx_mappings_raw(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Read raw YAML content of the OFX mappings file."""
    mappings_path = Path(config_manager.get_config().ofx_mappings_file)
    if not mappings_path.exists():
        return success_json_response(RuleContentResponse(filename=mappings_path.name, content=""))
    content = mappings_path.read_text(encoding="utf-8")
    return success_json_response(RuleContentResponse(filename=mappings_path.name, content=content))


@router.put(
    "/ofx-mappings",
    response_model=ApiResponse[RuleWriteResponse],
    operation_id="updateOfxMappings",
)
async def update_ofx_mappings(
    body: RuleWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """Validate and write the OFX mappings file with atomic write + backup."""
    mappings_path = Path(config_manager.get_config().ofx_mappings_file)

    # Parse YAML
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(io.StringIO(body.content))
    except Exception as e:
        raise APIError(
            message=f"YAML parse error: {e}",
            code=ec.YAML_PARSE_ERROR,
            status_code=400,
        )

    # Accept null/empty as empty list
    if data is None:
        data = []

    if not isinstance(data, list):
        raise APIError(
            message="OFX mappings file must be a YAML list of account mappings.",
            code=ec.VALIDATION_ERROR,
            status_code=400,
        )

    # Validate each entry
    for i, entry in enumerate(data):
        try:
            OFXAccountMapping.model_validate(entry)
        except (ValidationError, Exception) as e:
            raise APIError(
                message=f"OFX mapping entry {i + 1} is invalid: {e}",
                code=ec.VALIDATION_ERROR,
                status_code=400,
            )

    mappings_path.parent.mkdir(parents=True, exist_ok=True)
    file_existed = mappings_path.exists()

    if file_existed:
        with backup_manager.atomic_write(str(mappings_path)) as f:
            f.seek(0)
            f.truncate()
            f.write(body.content)
    else:
        mappings_path.write_text(body.content, encoding="utf-8")

    logger.info(f"Updated OFX mappings: {mappings_path}")

    return success_json_response(
        RuleWriteResponse(filename=mappings_path.name, path=str(mappings_path), backup_created=file_existed)
    )

