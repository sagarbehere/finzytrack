"""
Ledger Transaction Update Router
"""
import logging
from fastapi import APIRouter, Depends, Body
from datetime import datetime

from app.schemas.response_schemas import ApiResponse
from app.schemas.transaction_update_schemas import (
    UpdateTransactionRequest,
    UpdateTransactionResponse,
    UpdateTransaction,
)
from app.schemas.transaction_delete_schemas import (
    DeleteTransactionRequest,
    DeleteTransactionResponse,
)
from app.dependencies import get_beancount_manager, get_sqlite_reader, get_config_manager
from app.core.ledger_manager import LedgerManager
from app.core.config_manager import ConfigManager
from app.config import CategorizationEngine
from app.services.sqlite_reader import SqliteReader
from app.services.categorization import CategorizationInput, run_categorization
from app.services.ai_categorizer import AICategorizeError
from app.schemas.transaction_schemas import (
    CategorizeExistingRequest,
    CategorizeExistingResponse,
    CategorizeExistingResult,
    CategorizationStats,
)
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.helpers.transaction_validation import validate_postings
from app import error_codes as ec
from beancount.core.data import Transaction, Posting
from beancount.core.amount import Amount
from beancount.core.position import Cost
from decimal import Decimal
import time

logger = logging.getLogger(__name__)

router = APIRouter()


@router.put(
    "/transactions",
    response_model=ApiResponse[UpdateTransactionResponse],
    operation_id="updateLedgerTransactions"
)
async def update_ledger_transactions(
    request: UpdateTransactionRequest = Body(...),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """
    Update existing transactions in the ledger.

    This endpoint:
    1. Validates all transactions before making changes (atomic)
    2. Locates transactions by ID (UUIDv7) in the ledger
    3. Updates them atomically in the ledger file
    4. Returns success with count of updated transactions

    If any transaction fails validation, the entire operation is rolled back.
    """
    try:
        transactions_to_update = request.transactions

        if not transactions_to_update:
            raise APIError(
                message="No transactions provided",
                code=ec.NO_TRANSACTIONS,
                status_code=400
            )

        logger.info(f"Updating {len(transactions_to_update)} transactions")

        # Fetch account names once for the entire batch
        account_names = sqlite_reader.get_account_names()

        # Step 1: Validate all transactions
        validated_transactions = []
        for update_txn in transactions_to_update:
            try:
                # Validate account format/existence and cost/price field completeness
                validate_postings(
                    update_txn.postings,
                    txn_date=str(update_txn.date),
                    beancount_manager=beancount_manager,
                    account_names=account_names,
                )

                # Convert to Beancount Transaction object
                beancount_txn = _convert_to_beancount_transaction(update_txn)

                # Validate transaction (Beancount will check balancing, etc.)
                errors = beancount_manager.validate_transaction(beancount_txn)
                if errors:
                    raise APIError(
                        message=f"Validation failed for transaction {update_txn.id}",
                        code=ec.TRANSACTION_VALIDATION_FAILED,
                        status_code=400,
                        details={"errors": [str(e) for e in errors]}
                    )

                validated_transactions.append((update_txn.id, beancount_txn))

            except Exception as e:
                logger.error(f"Validation error for transaction {update_txn.id}: {e}")
                raise APIError(
                    message=f"Invalid transaction {update_txn.id}: {str(e)}",
                    code=ec.TRANSACTION_INVALID,
                    status_code=400,
                    details={"transaction_id": update_txn.id, "error": str(e)}
                )

        # Step 2: Locate and update transactions atomically
        updated_count = beancount_manager.update_transactions_by_id(
            validated_transactions
        )

        logger.info(f"Successfully updated {updated_count} transactions")

        return success_json_response(
            UpdateTransactionResponse(
                updated_count=updated_count,
                message=f"Successfully updated {updated_count} transaction(s)"
            )
        )

    except APIError:
        raise
    except Exception as e:
        logger.error(f"Failed to update transactions: {e}", exc_info=True)
        raise APIError(
            message="Failed to update transactions",
            code=ec.UPDATE_FAILED,
            status_code=500,
            details={"error": str(e)}
        )


def _convert_to_beancount_transaction(update_txn: UpdateTransaction) -> Transaction:
    """
    Convert UpdateTransaction to Beancount Transaction object.
    """
    # Parse date
    if isinstance(update_txn.date, str):
        date_obj = datetime.fromisoformat(update_txn.date).date()
    else:
        date_obj = update_txn.date

    # Build postings
    postings = []
    for p in update_txn.postings:
        # Build amount
        # Ensure at least 2 decimal places so Beancount infers proper tolerance
        raw_amount = Decimal(str(p.amount)) if p.amount is not None else None
        if raw_amount is not None and raw_amount == raw_amount.to_integral_value():
            raw_amount = raw_amount.quantize(Decimal('0.01'))
        amount = Amount(raw_amount, p.currency) if raw_amount is not None else None

        # Build cost
        cost = None
        if p.cost_amount is not None and p.cost_currency is not None and p.cost_date:
            # Cost requires amount, currency, and date to all be non-null
            cost = Cost(
                number=Decimal(str(p.cost_amount)),
                currency=p.cost_currency,
                date=datetime.fromisoformat(p.cost_date).date(),
                label=None
            )

        # Build price
        price = None
        if p.price_amount is not None and p.price_currency is not None:
            price = Amount(Decimal(str(p.price_amount)), p.price_currency)

        posting = Posting(
            account=p.account,
            units=amount,
            cost=cost,
            price=price,
            flag=None,
            meta=p.posting_meta or {}
        )
        postings.append(posting)

    # Build metadata
    meta = dict(update_txn.meta) if update_txn.meta else {}

    # Ensure ID is in metadata
    if 'id' not in meta:
        meta['id'] = update_txn.id

    # Add memo to metadata if present and non-trivial (skip placeholders like "-")
    memo = (update_txn.memo or '').strip()
    if memo and memo != '-':
        meta['memo'] = memo

    # Build transaction
    txn = Transaction(
        meta=meta,
        date=date_obj,
        flag=update_txn.flag,
        payee=update_txn.payee,
        narration=update_txn.narration,
        tags=frozenset(update_txn.tags) if update_txn.tags else frozenset(),
        links=frozenset(update_txn.links) if update_txn.links else frozenset(),
        postings=postings
    )

    return txn


@router.delete(
    "/transactions",
    response_model=ApiResponse[DeleteTransactionResponse],
    operation_id="deleteLedgerTransactions"
)
async def delete_ledger_transactions(
    request: DeleteTransactionRequest = Body(...),
    beancount_manager: LedgerManager = Depends(get_beancount_manager)
):
    """
    Delete transactions from the ledger by ID.

    This endpoint:
    1. Validates that all transaction IDs exist in the ledger
    2. Removes them atomically from the ledger file
    3. Returns success with count of deleted transactions

    If any transaction ID is not found, the entire operation is rolled back.
    """
    try:
        transaction_ids = request.transaction_ids

        if not transaction_ids:
            raise APIError(
                message="No transaction IDs provided",
                code=ec.NO_TRANSACTION_IDS,
                status_code=400
            )

        logger.info(f"Deleting {len(transaction_ids)} transaction(s)")

        # Delete transactions atomically
        deleted_count = beancount_manager.delete_transactions_by_id(transaction_ids)

        logger.info(f"Successfully deleted {deleted_count} transaction(s)")

        return success_json_response(
            DeleteTransactionResponse(
                deleted_count=deleted_count,
                message=f"Successfully deleted {deleted_count} transaction(s)"
            )
        )

    except APIError:
        raise
    except Exception as e:
        logger.error(f"Failed to delete transactions: {e}", exc_info=True)
        raise APIError(
            message="Failed to delete transactions",
            code=ec.DELETE_FAILED,
            status_code=500,
            details={"error": str(e)}
        )


@router.post(
    "/categorize",
    response_model=ApiResponse[CategorizeExistingResponse],
    operation_id="categorizeExistingTransactions",
)
async def categorize_existing_transactions(
    request: CategorizeExistingRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """
    Suggest accounts for transactions already in the ledger (e.g. bulk-resolving
    Expenses:Unknown). Uses the same engines as import categorization but runs NO
    duplicate detection — the rows already exist. source_account is provided
    per-transaction (the known posting's account) as AI prompt context.
    """
    config = config_manager.get_config()
    default_account = config.accounts.default_unknown_account
    warnings: list[str] = []
    start_time = time.monotonic()

    # Engine resolution (mirrors import categorize).
    if request.force_engine:
        try:
            engine = CategorizationEngine(request.force_engine)
        except ValueError:
            raise APIError(
                message=f"Invalid force_engine value: '{request.force_engine}'. Must be 'classifier', 'ai', or 'llm'.",
                code=ec.INVALID_ENGINE,
                status_code=422,
            )
    else:
        engine = config.ai.categorization.engine
    if engine == CategorizationEngine.LLM:
        engine = CategorizationEngine.AI

    account_names = sqlite_reader.get_account_names()
    training_data = sqlite_reader.get_training_data() if engine == CategorizationEngine.CLASSIFIER else []

    categorization_map: dict[str, tuple[str, float | None]] = {}
    engine_used = "default"
    ml_warning: str | None = None

    if not config.ai.categorization.enabled:
        for t in request.transactions:
            categorization_map[t.id] = (default_account, None)
    else:
        if engine == CategorizationEngine.AI and not config.ai.llm.is_configured:
            raise APIError(
                message="AI is not configured. Enable Finzytrack AI or set a model under Settings → AI.",
                code=ec.LLM_NOT_CONFIGURED,
                status_code=400,
            )
        cat_inputs = [
            CategorizationInput(
                id=t.id, payee=t.payee, memo=t.memo,
                narration=t.narration or "", source_account=t.source_account,
            )
            for t in request.transactions
        ]
        try:
            categorization_map, engine_used, cat_warnings, ml_warning = run_categorization(
                transactions=cat_inputs,
                engine=engine,
                account_names=account_names,
                training_data=training_data,
                default_account=default_account,
                llm_config=config.ai.llm,
            )
        except AICategorizeError as e:
            raise APIError(
                message=f"AI categorization failed: {e}",
                code=ec.AI_CATEGORIZE_FAILED,
                status_code=502,
            )
        warnings.extend(cat_warnings)

    # No duplicate detection — these transactions are already committed.
    results: list[CategorizeExistingResult] = []
    categorized_count = 0
    for t in request.transactions:
        account, confidence = categorization_map[t.id]
        if account != default_account:
            categorized_count += 1
        results.append(CategorizeExistingResult(id=t.id, suggested_category=account, confidence=confidence))

    stats = CategorizationStats(
        total_count=len(request.transactions),
        categorized_count=categorized_count,
        duplicate_count=0,
        engine_used=engine_used,
        duration_secs=round(time.monotonic() - start_time, 1),
        ml_training_info=ml_warning,
        warnings=warnings,
    )
    return success_json_response(CategorizeExistingResponse(results=results, stats=stats))
