from fastapi import APIRouter, Depends
from app.config import OFXAccountMapping
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.dependencies import get_config_manager, get_beancount_manager
from app.schemas.ofx_schemas import (
    OFXDetectionRequest,
    OFXDetectionData,
    LearnOFXAccountRequest,
    LearnOFXAccountData
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

router = APIRouter()

@router.post("/detect-ofx-account", response_model=ApiResponse[OFXDetectionData], operation_id="detectOfxAccount")
async def detect_ofx_account(
    request: OFXDetectionRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    config = config_manager.get_config()
    try:
        detailed_accounts = beancount_manager.get_detailed_accounts()
        if not detailed_accounts:
            # This is a specific case where the ledger exists but is empty or uninitialized.
            raise APIError(
                message="Ledger file contains no open accounts.",
                code="RESOURCE_NOT_FOUND",
                status_code=404,
                details={"resource_type": "account", "ledger_file": config.ledger_file}
            )
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code="FILE_NOT_FOUND", status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code="FILE_PERMISSION_ERROR", status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to read ledger file: {e}", code="UNKNOWN_SERVER_ERROR", status_code=500, details={"path": config.ledger_file})
    
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
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    config = config_manager.get_config()
    if not beancount_manager.validate_account_format(request.beancount_account):
        raise APIError(
            message="Invalid account format",
            code="VALIDATION_ERROR",
            status_code=422,
            details={
                "field": "beancount_account",
                "account_name": request.beancount_account,
                "help": "Account name must follow Beancount naming conventions"
            }
        )
    
    try:
        account_exists = beancount_manager.is_existing_account(request.beancount_account)
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code="FILE_NOT_FOUND", status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code="FILE_PERMISSION_ERROR", status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to validate account: {e}", code="UNKNOWN_SERVER_ERROR", status_code=500, details={"path": config.ledger_file})
    
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
                code="RESOURCE_CONFLICT",
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
            code="CONFIG_SAVE_ERROR",
            status_code=403,
            details={"path": getattr(config, 'config_path', 'config.yaml')}
        )
    except Exception as e:
        raise APIError(
            message="Failed to save mapping to configuration file",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500,
            details={"error": str(e)}
        )

