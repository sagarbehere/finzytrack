from fastapi import APIRouter, Depends
import logging
import time
from decimal import Decimal

from beancount.core import data, amount
from beancount.core.position import Cost
from beancount.parser import parser

from app.config import CategorizationEngine
from app.core.config_manager import ConfigManager
from app.core.beancount_manager import BeancountManager
from app.dependencies import get_config_manager, get_beancount_manager
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.schemas.transaction_schemas import (
    CategorizeRequest,
    CategorizeResponse,
    CategorizedTransactionResult,
    CategorizationStats,
    CommitRequest,
    CommitResponse
)
from app.helpers.response_helpers import success_json_response
from app.services.categorizer import initialize_classifier, categorize_transaction
from app.services.ai_categorizer import categorize_transactions_ai, AICategorizeError
from app.services.duplicate_detector import find_duplicate
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/categorize", response_model=ApiResponse[CategorizeResponse])
async def categorize_transactions(
    request: CategorizeRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Categorize transactions and detect potential duplicates.

    Engine selection:
    - force_engine in request overrides config (for one-time AI fallback)
    - config.ai.categorization.engine determines default engine
    - If engine=classifier but insufficient training data, returns a warning
      with ai_fallback_available flag so frontend can offer AI fallback

    Args:
        request: CategorizeRequest with transactions to process
        config_manager: Injected config manager
        beancount_manager: Injected beancount manager (provides cached data)

    Returns:
        CategorizeResponse with results and stats
    """
    config = config_manager.get_config()
    default_account = config.accounts.default_unknown_account
    warnings: list[str] = []
    start_time = time.monotonic()

    # Determine which engine to use
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
    # Normalize LLM alias to AI
    if engine == CategorizationEngine.LLM:
        engine = CategorizationEngine.AI

    # Get cached data (only fetch what's needed for the selected engine)
    existing_transactions = beancount_manager.cache.get_transactions()
    account_names = beancount_manager.cache.get_account_names()
    training_data = beancount_manager.cache.get_training_data() if engine == CategorizationEngine.CLASSIFIER else []
    logger.info(f"Categorization engine={engine.value}, "
               f"{len(existing_transactions)} existing transactions, "
               f"{len(account_names)} accounts")

    # ── Categorization ────────────────────────────────────────────────
    # Maps transaction id -> (suggested_category, confidence)
    categorization_map: dict[str, tuple[str, float | None]] = {}
    engine_used = "default"
    ml_warning: str | None = None

    if not config.ai.categorization.enabled:
        # Categorization disabled — all get default
        engine_used = "default"
        for raw_txn in request.transactions:
            categorization_map[raw_txn.id] = (default_account, None)

    elif engine == CategorizationEngine.AI:
        # AI engine
        engine_used = "ai"
        if not config.ai.llm.model:
            raise APIError(
                message="AI is not configured. Go to Settings and configure the AI section (ai.llm.model must be set).",
                code=ec.LLM_NOT_CONFIGURED,
                status_code=400,
            )

        txn_dicts = [
            {"id": t.id, "payee": t.payee, "memo": t.memo or "", "narration": t.narration or ""}
            for t in request.transactions
        ]

        try:
            ai_results, ai_warnings = categorize_transactions_ai(
                transactions=txn_dicts,
                account_names=account_names,
                default_account=default_account,
                source_account=request.source_account,
                llm_config=config.ai.llm,
            )
            warnings.extend(ai_warnings)
            for raw_txn in request.transactions:
                account = ai_results.get(raw_txn.id, default_account)
                categorization_map[raw_txn.id] = (account, None)
        except AICategorizeError as e:
            raise APIError(
                message=f"AI categorization failed: {e}",
                code=ec.AI_CATEGORIZE_FAILED,
                status_code=502,
            )

    else:
        # Classifier engine
        classifier, ml_warning = initialize_classifier(
            training_data=training_data,
            ml_enabled=True,
        )

        if classifier:
            engine_used = "classifier"
            for raw_txn in request.transactions:
                description_parts = [raw_txn.payee]
                if raw_txn.memo:
                    description_parts.append(raw_txn.memo)
                if raw_txn.narration:
                    description_parts.append(raw_txn.narration)
                description = " ".join(description_parts)
                suggested_category, confidence = categorize_transaction(description, classifier)
                categorization_map[raw_txn.id] = (suggested_category, confidence)
        else:
            # Classifier has insufficient data — use default account
            engine_used = "default"
            if ml_warning:
                warnings.append(ml_warning)
            for raw_txn in request.transactions:
                categorization_map[raw_txn.id] = (default_account, None)

    # ── Duplicate detection (always runs, regardless of engine) ───────
    results = []
    categorized_count = 0
    duplicate_count = 0

    for raw_txn in request.transactions:
        suggested_category, confidence = categorization_map[raw_txn.id]
        if suggested_category != default_account:
            categorized_count += 1

        is_duplicate, duplicate_info = find_duplicate(
            txn_date=raw_txn.date,
            payee=raw_txn.payee,
            amount=raw_txn.amount,
            source_account=request.source_account,
            narration=raw_txn.narration or "",
            existing_transactions=existing_transactions,
            external_id=raw_txn.external_id,
            external_id_type=raw_txn.external_id_type,
        )

        if is_duplicate:
            duplicate_count += 1

        result = CategorizedTransactionResult(
            id=raw_txn.id,
            suggested_category=suggested_category,
            confidence=confidence,
            is_duplicate=is_duplicate,
            duplicate_info=duplicate_info,
        )
        results.append(result)

    duration_secs = round(time.monotonic() - start_time, 1)

    stats = CategorizationStats(
        total_count=len(request.transactions),
        categorized_count=categorized_count,
        duplicate_count=duplicate_count,
        engine_used=engine_used,
        duration_secs=duration_secs,
        ml_training_info=ml_warning,
        warnings=warnings,
    )

    return success_json_response(CategorizeResponse(results=results, stats=stats))


@router.post("/commit", response_model=ApiResponse[CommitResponse])
async def commit_transactions(
    request: CommitRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Commit transactions to the Beancount ledger.

    This endpoint:
    1. Validates each transaction with Beancount
    2. Generates transaction IDs (UUIDv7 + content_hash)
    3. Formats transactions with proper Beancount syntax
    4. Atomically writes to ledger with backup

    Args:
        request: CommitRequest with transactions to commit
        config_manager: Injected config manager
        beancount_manager: Injected beancount manager

    Returns:
        CommitResponse with success status and count

    Raises:
        APIError: If validation or write fails
    """

    # Validated Beancount transaction objects to append
    validated_transactions = []

    # Process each transaction
    for commit_txn in request.transactions:
        # Validate account names
        for posting in commit_txn.postings:
            if not beancount_manager.validate_account_format(posting.account):
                raise APIError(
                    message=f"Invalid account format: {posting.account}",
                    code=ec.INVALID_ACCOUNT_FORMAT,
                    status_code=422,
                    details={"account": posting.account, "date": str(commit_txn.date)}
                )

            if not beancount_manager.is_existing_account(posting.account):
                raise APIError(
                    message=f"Account does not exist in ledger: {posting.account}",
                    code=ec.ACCOUNT_NOT_FOUND,
                    status_code=422,
                    details={
                        "account": posting.account,
                        "date": str(commit_txn.date),
                        "suggestion": "Create the account first using the Accounts page"
                    }
                )

        # Validate cost and price fields for each posting
        for posting in commit_txn.postings:
            # Validate cost completeness (treat 0 as empty)
            cost_amount_is_nonzero = posting.cost_amount is not None and Decimal(str(posting.cost_amount)) != 0
            if cost_amount_is_nonzero:
                if posting.cost_currency is None:
                    raise APIError(
                        message="Cost amount specified but cost currency missing",
                        code=ec.INVALID_COST,
                        status_code=422,
                        details={
                            "account": posting.account,
                            "date": str(commit_txn.date),
                            "cost_amount": str(posting.cost_amount)
                        }
                    )
            elif posting.cost_currency is not None:
                raise APIError(
                    message="Cost currency specified but cost amount missing or zero",
                    code=ec.INVALID_COST,
                    status_code=422,
                    details={"account": posting.account, "date": str(commit_txn.date)}
                )

            # Validate price completeness (treat 0 as empty)
            price_amount_is_nonzero = posting.price_amount is not None and Decimal(str(posting.price_amount)) != 0
            if price_amount_is_nonzero:
                if posting.price_currency is None or posting.price_type is None:
                    raise APIError(
                        message="Price amount specified but price currency or type missing",
                        code=ec.INVALID_PRICE,
                        status_code=422,
                        details={
                            "account": posting.account,
                            "date": str(commit_txn.date),
                            "price_amount": str(posting.price_amount),
                            "price_currency": posting.price_currency,
                            "price_type": posting.price_type
                        }
                    )
            elif posting.price_currency is not None or posting.price_type is not None:
                raise APIError(
                    message="Price currency or type specified but price amount missing or zero",
                    code=ec.INVALID_PRICE,
                    status_code=422,
                    details={"account": posting.account, "date": str(commit_txn.date)}
                )

            # Validate price type
            if posting.price_type is not None and posting.price_type not in ['@', '@@']:
                raise APIError(
                    message=f"Invalid price type: {posting.price_type} (must be '@' or '@@')",
                    code=ec.INVALID_PRICE_TYPE,
                    status_code=422,
                    details={"account": posting.account, "date": str(commit_txn.date)}
                )

        # Create Beancount transaction object for validation
        try:
            # Create postings
            beancount_postings = []
            for posting in commit_txn.postings:
                # Ensure at least 2 decimal places so Beancount infers proper
                # tolerance (integer amounts like 85000 would infer zero tolerance,
                # causing price-converted postings to fail balance checks).
                raw_amount = Decimal(str(posting.amount))
                if raw_amount == raw_amount.to_integral_value():
                    raw_amount = raw_amount.quantize(Decimal('0.01'))
                posting_amount = amount.Amount(raw_amount, posting.currency)

                # Create cost object if cost fields present and amount is non-zero
                posting_cost = None
                if posting.cost_amount is not None and Decimal(str(posting.cost_amount)) != 0:
                    # Validation ensures cost_currency is non-None when cost_amount is set
                    assert posting.cost_currency is not None
                    posting_cost = Cost(
                        number=Decimal(str(posting.cost_amount)),
                        currency=posting.cost_currency,
                        date=posting.cost_date,  # type: ignore[arg-type]
                        label=None
                    )

                # Create price object if price fields present and amount is non-zero
                posting_price = None
                if posting.price_amount is not None and Decimal(str(posting.price_amount)) != 0:
                    # Validation ensures price_currency is non-None when price_amount is set
                    assert posting.price_currency is not None
                    # For @@ (total price), we need to divide by units to get per-unit price
                    # because Beancount's price field is always per-unit
                    price_value = Decimal(str(posting.price_amount))
                    if posting.price_type == '@@':
                        # Total price: divide by units to get per-unit price.
                        # Round to the fewest decimal places where
                        # |units × rounded_price - total| < 0.005 (Beancount's
                        # default tolerance for 2-decimal currencies like USD).
                        units_value = abs(Decimal(str(posting.amount)))
                        if units_value != 0:
                            tolerance = Decimal('0.005')
                            raw = price_value / units_value
                            for places in range(2, 29):
                                quantized = raw.quantize(Decimal(10) ** -places)
                                if abs(units_value * quantized - price_value) < tolerance:
                                    price_value = quantized
                                    break
                            else:
                                price_value = raw  # fallback: use full precision
                        else:
                            raise APIError(
                                message="Cannot use @@ price with zero units",
                                code=ec.INVALID_PRICE,
                                status_code=422,
                                details={"account": posting.account, "date": str(commit_txn.date)}
                            )

                    posting_price = amount.Amount(
                        price_value,
                        posting.price_currency
                    )

                # Create posting metadata
                posting_meta = posting.posting_meta or {}

                beancount_posting = data.Posting(
                    account=posting.account,
                    units=posting_amount,
                    cost=posting_cost,
                    price=posting_price,
                    flag=None,
                    meta=posting_meta
                )
                beancount_postings.append(beancount_posting)

            # Prepare additional metadata from request
            additional_meta = dict(commit_txn.meta)

            # Add memo metadata if present and non-trivial (skip placeholders like "-")
            memo = (commit_txn.memo or '').strip()
            if memo and memo != '-':
                additional_meta['memo'] = memo

            # Extract external ID fields from metadata
            external_id = additional_meta.pop('external_id', None)
            external_id_type = additional_meta.pop('external_id_type', None)
            # Legacy fallback: read old ofx_id key if present
            if not external_id:
                legacy_ofx_id = additional_meta.pop('ofx_id', None)
                if legacy_ofx_id:
                    external_id = legacy_ofx_id
                    external_id_type = 'OFX'

            # Create transaction with new UUIDv7 + content_hash ID system
            # This handles all ID generation and metadata setup
            beancount_txn_with_id = beancount_manager.create_transaction_with_ids(
                date_obj=commit_txn.date,
                payee=commit_txn.payee,
                narration=commit_txn.narration,
                postings=beancount_postings,
                source_account=commit_txn.source_account,
                flag=commit_txn.flag,
                external_id=external_id,
                external_id_type=external_id_type,
                additional_meta=additional_meta
            )

            # Validate transaction balance
            # Beancount will check balance during validation
            test_string = _format_beancount_transaction(beancount_txn_with_id, include_transaction_id=False)

            # Load and validate with Beancount
            entries, errors, _ = parser.parse_string(test_string)

            if errors:
                error_messages = [str(error.message) for error in errors]
                raise APIError(
                    message=f"Transaction validation failed: {'; '.join(error_messages)}",
                    code=ec.TRANSACTION_VALIDATION_FAILED,
                    status_code=422,
                    details={
                        "date": str(commit_txn.date),
                        "payee": commit_txn.payee,
                        "errors": error_messages
                    }
                )

            # Collect validated transaction for writing
            validated_transactions.append(beancount_txn_with_id)

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to create Beancount transaction: {e}")
            raise APIError(
                message=f"Failed to create transaction: {str(e)}",
                code=ec.TRANSACTION_CREATION_FAILED,
                status_code=500,
                details={"date": str(commit_txn.date), "error": str(e)}
            )

    # Write all transactions to ledger via the single write path
    try:
        beancount_manager.append_entries(validated_transactions)
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
            code=ec.LEDGER_WRITE_FAILED,
            status_code=500,
            details={"error": str(e)}
        )


def _format_beancount_transaction(txn: data.Transaction, include_transaction_id: bool = True) -> str:
    """
    Format a Beancount transaction object as a string.

    Uses Beancount's built-in printer for accurate formatting,
    with optional exclusion of ID fields for validation.

    Args:
        txn: Beancount transaction object
        include_transaction_id: Whether to include id/content_hash/transaction_id metadata

    Returns:
        Formatted transaction string
    """
    from beancount.parser import printer

    # If we need to exclude IDs, create a copy without them
    if not include_transaction_id and txn.meta:
        # Create new meta dict without ID fields
        new_meta = {
            k: v for k, v in txn.meta.items()
            if k not in ('id', 'content_hash', 'transaction_id')
        }
        # Create new transaction with modified meta
        txn = txn._replace(meta=new_meta)

    # Use Beancount's built-in printer for accurate formatting
    # This handles cost, price, posting metadata, etc. correctly
    # Note: format_entry() already includes a trailing newline
    formatted = printer.format_entry(txn)

    return formatted
