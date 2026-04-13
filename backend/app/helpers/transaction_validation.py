"""
Shared validation for transaction postings.

Used by both the commit (new transactions) and update (existing transactions)
endpoints to enforce consistent rules.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol, Set

from app.exceptions import APIError
from app import error_codes as ec


class PostingLike(Protocol):
    """Fields common to CommitPosting and UpdatePosting."""
    account: str
    cost_amount: Optional[Decimal]
    cost_currency: Optional[str]
    price_amount: Optional[Decimal]
    price_currency: Optional[str]
    price_type: Optional[str]


def validate_postings(
    postings: list[PostingLike],
    *,
    txn_date: str,
    beancount_manager: object,
    account_names: Set[str],
) -> None:
    """
    Validate posting-level fields. Raises APIError on first failure.

    Checks:
    1. Account format (Beancount naming rules)
    2. Account existence in the ledger
    3. Cost field completeness
    4. Price field completeness
    5. Price type validity
    """
    for posting in postings:
        _validate_account(posting, txn_date, beancount_manager, account_names)

    for posting in postings:
        _validate_cost_fields(posting, txn_date)
        _validate_price_fields(posting, txn_date)


def _validate_account(
    posting: PostingLike,
    txn_date: str,
    beancount_manager: object,
    account_names: Set[str],
) -> None:
    if not beancount_manager.validate_account_format(posting.account):  # type: ignore[attr-defined]
        raise APIError(
            message=f"Invalid account format: {posting.account}",
            code=ec.INVALID_ACCOUNT_FORMAT,
            status_code=422,
            details={"account": posting.account, "date": txn_date},
        )

    if posting.account not in account_names:
        raise APIError(
            message=f"Account does not exist in ledger: {posting.account}",
            code=ec.ACCOUNT_NOT_FOUND,
            status_code=422,
            details={
                "account": posting.account,
                "date": txn_date,
                "suggestion": "Create the account first using the Accounts page",
            },
        )


def _validate_cost_fields(posting: PostingLike, txn_date: str) -> None:
    cost_amount_is_nonzero = (
        posting.cost_amount is not None and Decimal(str(posting.cost_amount)) != 0
    )
    if cost_amount_is_nonzero:
        if posting.cost_currency is None:
            raise APIError(
                message="Cost amount specified but cost currency missing",
                code=ec.INVALID_COST,
                status_code=422,
                details={
                    "account": posting.account,
                    "date": txn_date,
                    "cost_amount": str(posting.cost_amount),
                },
            )
    elif posting.cost_currency is not None:
        raise APIError(
            message="Cost currency specified but cost amount missing or zero",
            code=ec.INVALID_COST,
            status_code=422,
            details={"account": posting.account, "date": txn_date},
        )


def _validate_price_fields(posting: PostingLike, txn_date: str) -> None:
    price_amount_is_nonzero = (
        posting.price_amount is not None and Decimal(str(posting.price_amount)) != 0
    )
    if price_amount_is_nonzero:
        if posting.price_currency is None or posting.price_type is None:
            raise APIError(
                message="Price amount specified but price currency or type missing",
                code=ec.INVALID_PRICE,
                status_code=422,
                details={
                    "account": posting.account,
                    "date": txn_date,
                    "price_amount": str(posting.price_amount),
                    "price_currency": posting.price_currency,
                    "price_type": posting.price_type,
                },
            )
    elif posting.price_currency is not None or posting.price_type is not None:
        raise APIError(
            message="Price currency or type specified but price amount missing or zero",
            code=ec.INVALID_PRICE,
            status_code=422,
            details={"account": posting.account, "date": txn_date},
        )

    if posting.price_type is not None and posting.price_type not in ["@", "@@"]:
        raise APIError(
            message=f"Invalid price type: {posting.price_type} (must be '@' or '@@')",
            code=ec.INVALID_PRICE_TYPE,
            status_code=422,
            details={"account": posting.account, "date": txn_date},
        )
