from fastapi import APIRouter, Depends, Request
from app.config import OFXAccountMapping
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.exceptions import APIError
from app.dependencies import get_config_manager, get_beancount_manager
from app.schemas.import_schemas import (
    OFXDetectionRequest,
    DetectionData,
    LearnAccountRequest,
    LearnAccountData,
    CreateAccountRequest,
    CreateAccountData
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

router = APIRouter()

@router.post("/detect-account", response_model=ApiResponse[DetectionData], operation_id="detectAccount")
async def detect_account(
    request: OFXDetectionRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    config = config_manager.get_config()
    try:
        accounts = beancount_manager.get_accounts()
        if not accounts:
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
    
    for mapping in config.ofx_account_mappings:
        if (mapping.institution.lower() == request.institution.lower() and
            (mapping.institution_fid or "").lower() == (request.institution_fid or "").lower() and
            mapping.account_type.lower() == request.account_type.lower() and
            mapping.account_id == request.account_id):
            
            detection_data = DetectionData(
                detected=True,
                beancount_account=mapping.beancount_account,
                currency=mapping.currency,
                message=f"Account detected successfully: {mapping.beancount_account}"
            )
            return success_json_response(detection_data)
    
    detection_data = DetectionData(
        detected=False,
        beancount_account=config.defaults.unknown_account,
        currency=config.defaults.currency,
        message="No matching account configuration found. Please verify account details."
    )
    return success_json_response(detection_data)

@router.post("/learn-account", response_model=ApiResponse[LearnAccountData], operation_id="learnAccount")
async def learn_account(
    request: LearnAccountRequest,
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
        existing_accounts = beancount_manager.get_accounts()
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code="FILE_NOT_FOUND", status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code="FILE_PERMISSION_ERROR", status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to validate account: {e}", code="UNKNOWN_SERVER_ERROR", status_code=500, details={"path": config.ledger_file})
    
    if request.beancount_account not in existing_accounts:
        learn_data = LearnAccountData(
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
    
    for existing_mapping in config.ofx_account_mappings:
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
        learn_data = LearnAccountData(
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

@router.post("/create-account", response_model=ApiResponse[CreateAccountData], status_code=201, operation_id="createAccount")
async def create_account(
    request: CreateAccountRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    config = config_manager.get_config()
    if not beancount_manager.validate_account_format(request.account_name):
        raise APIError(
            message="Invalid account format",
            code="VALIDATION_ERROR",
            status_code=422,
            details={
                "field": "account_name",
                "account_name": request.account_name,
                "help": "Account name must follow Beancount naming conventions"
            }
        )
    
    try:
        existing_accounts = beancount_manager.get_accounts()
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code="FILE_NOT_FOUND", status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(message="Permission denied accessing ledger file", code="FILE_PERMISSION_ERROR", status_code=403, details={"path": config.ledger_file})
    except Exception as e:
        raise APIError(message=f"Failed to read accounts from ledger: {e}", code="UNKNOWN_SERVER_ERROR", status_code=500, details={"path": config.ledger_file})
    
    if request.account_name in existing_accounts:
        create_data = CreateAccountData(
            account_created=False
        )
        return success_json_response(create_data, status_code=200)
    
    try:
        beancount_manager.create_account(
            request.account_name,
            request.currency
        )
        
        create_data = CreateAccountData(
            account_created=True
        )
        return success_json_response(create_data, status_code=201)
            
    except FileNotFoundError:
        raise APIError(message="Ledger file not found", code="FILE_NOT_FOUND", status_code=404, details={"path": config.ledger_file})
    except PermissionError:
        raise APIError(
            message="Permission denied writing to ledger file",
            code="FILE_PERMISSION_ERROR",
            status_code=403,
            details={"path": config.ledger_file}
        )
    except Exception as e:
        if "syntax" in str(e).lower() or "parse" in str(e).lower():
            raise APIError(
                message="Ledger file contains syntax errors",
                code="FILE_SYNTAX_ERROR",
                status_code=422,
                details={"path": config.ledger_file, "error": str(e)}
            )
        else:
            raise APIError(
                message=f"Error creating account: {str(e)}",
                code="UNKNOWN_SERVER_ERROR",
                status_code=500,
                details={"path": config.ledger_file, "operation": "create_account"}
            )
