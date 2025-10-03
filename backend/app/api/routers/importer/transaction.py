from fastapi import APIRouter, Depends
import logging
from decimal import Decimal

from beancount.core import data, amount
from beancount.parser import parser

from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.core.beancount_manager import BeancountManager
from app.dependencies import get_config_manager, get_backup_manager, get_beancount_manager
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.schemas.transaction_schemas import (
    CategorizeRequest,
    CategorizeResponse,
    CategorizedTransactionResult,
    CategorizationStats,
    DuplicateInfo,
    CommitRequest,
    CommitResponse
)
from app.helpers.response_helpers import success_json_response
from app.services.categorizer import get_or_train_classifier, categorize_transaction
from app.services.duplicate_detector import load_existing_transactions, find_duplicate
from app.libs.transaction_id_generator import add_transaction_id_to_beancount_transaction

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/categorize", response_model=ApiResponse[CategorizeResponse])
async def categorize_transactions(
    request: CategorizeRequest,
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Categorize transactions using ML and detect potential duplicates.

    This endpoint:
    1. Trains ML classifier (or skips if disabled/insufficient data)
    2. Categorizes each transaction using ML or fallback to default
    3. Checks for duplicates using OFX ID and fuzzy matching
    4. Returns results in same order as request with statistics

    Args:
        request: CategorizeRequest with transactions to process
        config_manager: Injected config manager

    Returns:
        CategorizeResponse with results and stats
    """
    config = config_manager.get_config()

    # Initialize ML classifier
    classifier, ml_warning = get_or_train_classifier(
        ledger_file=config.ledger_file,
        ml_enabled=config.ml.enabled
    )

    # Load existing transactions for duplicate detection
    try:
        existing_transactions = load_existing_transactions(config.ledger_file)
        logger.info(f"Loaded {len(existing_transactions)} existing transactions for duplicate detection")
    except FileNotFoundError:
        logger.warning("Ledger file not found, skipping duplicate detection")
        existing_transactions = []
    except Exception as e:
        logger.error(f"Failed to load existing transactions: {e}")
        existing_transactions = []

    # Process each transaction
    results = []
    categorized_count = 0
    duplicate_count = 0

    for raw_txn in request.transactions:
        # Categorize using ML or default
        if classifier:
            # Combine payee and memo for ML classification
            description_parts = [raw_txn.payee]
            if raw_txn.memo:
                description_parts.append(raw_txn.memo)
            description = " ".join(description_parts)
            suggested_category, confidence = categorize_transaction(description, classifier)
            categorized_count += 1
        else:
            # Use default category with zero confidence
            suggested_category = config.accounts.default_unknown_account
            confidence = 0.0

        # Detect duplicates
        is_duplicate, duplicate_info = find_duplicate(
            txn_date=raw_txn.date,
            payee=raw_txn.payee,
            amount=raw_txn.amount,
            source_account=request.source_account,
            ofx_id=raw_txn.ofx_id,
            existing_transactions=existing_transactions
        )

        if is_duplicate:
            duplicate_count += 1

        # Create result
        result = CategorizedTransactionResult(
            date=raw_txn.date,
            amount=raw_txn.amount,
            suggested_category=suggested_category,
            confidence=confidence,
            is_duplicate=is_duplicate,
            duplicate_info=duplicate_info
        )

        results.append(result)

    # Create statistics
    stats = CategorizationStats(
        total_count=len(request.transactions),
        categorized_count=categorized_count,
        duplicate_count=duplicate_count,
        ml_training_info=ml_warning
    )

    # Create response
    response_data = CategorizeResponse(
        results=results,
        stats=stats
    )

    return success_json_response(response_data)


@router.post("/commit", response_model=ApiResponse[CommitResponse])
async def commit_transactions(
    request: CommitRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Commit transactions to the Beancount ledger.

    This endpoint:
    1. Validates each transaction with Beancount
    2. Generates transaction IDs
    3. Formats transactions with proper Beancount syntax
    4. Atomically writes to ledger with backup

    Args:
        request: CommitRequest with transactions to commit
        config_manager: Injected config manager
        backup_manager: Injected backup manager
        beancount_manager: Injected beancount manager

    Returns:
        CommitResponse with success status and count

    Raises:
        APIError: If validation or write fails
    """
    config = config_manager.get_config()

    # Formatted transaction strings
    formatted_transactions = []

    # Process each transaction
    for commit_txn in request.transactions:
        # Validate account names
        for posting in commit_txn.postings:
            if not beancount_manager.validate_account_format(posting.account):
                raise APIError(
                    message=f"Invalid account format: {posting.account}",
                    code="INVALID_ACCOUNT_FORMAT",
                    status_code=422,
                    details={"account": posting.account, "date": str(commit_txn.date)}
                )

            if not beancount_manager.is_existing_account(posting.account):
                raise APIError(
                    message=f"Account does not exist in ledger: {posting.account}",
                    code="ACCOUNT_NOT_FOUND",
                    status_code=422,
                    details={
                        "account": posting.account,
                        "date": str(commit_txn.date),
                        "suggestion": "Create the account first using the Accounts page"
                    }
                )

        # Create Beancount transaction object for validation
        try:
            # Create postings
            beancount_postings = []
            for posting in commit_txn.postings:
                posting_amount = amount.Amount(
                    Decimal(str(posting.amount)),
                    posting.currency
                )
                beancount_posting = data.Posting(
                    account=posting.account,
                    units=posting_amount,
                    cost=None,
                    price=None,
                    flag=None,
                    meta={}
                )
                beancount_postings.append(beancount_posting)

            # Create metadata
            meta = {}
            if commit_txn.ofx_id:
                meta['ofx_id'] = commit_txn.ofx_id
            if commit_txn.memo:
                meta['ofx_memo'] = commit_txn.memo
            meta['source_account'] = commit_txn.source_account

            # Create transaction
            beancount_txn = data.Transaction(
                meta=meta,
                date=commit_txn.date,
                flag=commit_txn.flag,
                payee=commit_txn.payee,
                narration=commit_txn.narration,
                tags=frozenset(commit_txn.tags),
                links=frozenset(commit_txn.links),
                postings=beancount_postings
            )

            # Validate transaction balance
            # Beancount will check balance during validation
            test_string = _format_beancount_transaction(beancount_txn, include_transaction_id=False)

            # Load and validate with Beancount
            entries, errors, _ = parser.parse_string(test_string)

            if errors:
                error_messages = [str(error.message) for error in errors]
                raise APIError(
                    message=f"Transaction validation failed: {'; '.join(error_messages)}",
                    code="TRANSACTION_VALIDATION_FAILED",
                    status_code=422,
                    details={
                        "date": str(commit_txn.date),
                        "payee": commit_txn.payee,
                        "errors": error_messages
                    }
                )

            # Add transaction ID
            beancount_txn_with_id = add_transaction_id_to_beancount_transaction(beancount_txn)

            # Format for writing
            formatted_txn = _format_beancount_transaction(beancount_txn_with_id, include_transaction_id=True)
            formatted_transactions.append(formatted_txn)

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to create Beancount transaction: {e}")
            raise APIError(
                message=f"Failed to create transaction: {str(e)}",
                code="TRANSACTION_CREATION_FAILED",
                status_code=500,
                details={"date": str(commit_txn.date), "error": str(e)}
            )

    # Write all transactions to ledger atomically
    try:
        with backup_manager.atomic_write(config.ledger_file) as f:
            current_content = f.read()

            # Ensure proper spacing
            if current_content and not current_content.endswith('\n'):
                current_content += '\n'
            if current_content and not current_content.endswith('\n\n'):
                current_content += '\n'

            # Append all transactions
            new_content = current_content + '\n'.join(formatted_transactions)

            # Write back
            f.seek(0)
            f.write(new_content)
            f.truncate()

        logger.info(f"Successfully committed {len(request.transactions)} transactions to ledger")

        return success_json_response(
            CommitResponse(
                success=True,
                count=len(request.transactions),
                message=f"Successfully committed {len(request.transactions)} transactions"
            )
        )

    except Exception as e:
        logger.error(f"Failed to write to ledger: {e}")
        raise APIError(
            message=f"Failed to write to ledger: {str(e)}",
            code="LEDGER_WRITE_FAILED",
            status_code=500,
            details={"error": str(e)}
        )


def _format_beancount_transaction(txn: data.Transaction, include_transaction_id: bool = True) -> str:
    """
    Format a Beancount transaction object as a string.

    Args:
        txn: Beancount transaction object
        include_transaction_id: Whether to include transaction_id metadata

    Returns:
        Formatted transaction string
    """
    lines = []

    # Transaction header
    payee_str = f'"{txn.payee}"' if txn.payee else '""'
    narration_str = f'"{txn.narration}"' if txn.narration else '""'
    header = f"{txn.date} {txn.flag} {payee_str} {narration_str}"
    lines.append(header)

    # Add metadata
    if include_transaction_id and txn.meta and 'transaction_id' in txn.meta:
        lines.append(f'  transaction_id: "{txn.meta["transaction_id"]}"')

    if txn.meta and 'source_account' in txn.meta:
        lines.append(f'  source_account: "{txn.meta["source_account"]}"')

    if txn.meta and 'ofx_id' in txn.meta:
        lines.append(f'  ofx_id: "{txn.meta["ofx_id"]}"')

    if txn.meta and 'ofx_memo' in txn.meta:
        lines.append(f'  ofx_memo: "{txn.meta["ofx_memo"]}"')

    # Add postings
    for posting in txn.postings:
        if posting.units:
            amount_str = f"{posting.units.number:.2f} {posting.units.currency}"
            lines.append(f"  {posting.account:<50} {amount_str}")
        else:
            # Posting without amount (balance assertion)
            lines.append(f"  {posting.account}")

    return '\n'.join(lines) + '\n'
