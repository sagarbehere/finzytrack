"""deposit_opener — the Open Deposit quick action's rules.

Turns an ``OpenDepositRequest`` into what gets written: the deposit's ``open``
(with ``account-type`` and its details), optionally its own interest income
account's ``open`` (``income-type: "interest"``, linked from the deposit by
``interest_account``), and optionally the transfer that funds it. Pure: it reads
a snapshot of the existing accounts and returns entries; the caller writes them
(``LedgerManager.open_deposit``, one write) or renders them as a preview.

Names are never classified: the only name rules are Beancount's (format, root
type) and "doesn't already exist".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import hashlib

from app import error_codes as ec
from app.schemas.investment_schemas import OpenDepositRequest


class AccountInfo(NamedTuple):
    closed: bool
    metadata: Dict[str, Any]
    currencies: Tuple[str, ...]
    open_date: Optional[date] = None


class DepositError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass
class OpenSpec:
    name: str
    open_date: date
    currencies: List[str]
    metadata: Dict[str, Any]


@dataclass
class DepositPlan:
    opens: List[OpenSpec]
    # (date, narration, [(account, amount, currency)], source_account), or None
    transfer: Optional[Tuple[date, str, List[Tuple[str, Decimal, str]], str]]
    warnings: List[str] = field(default_factory=list)


def _root(name: str) -> str:
    return name.split(":", 1)[0]


def plan_open_deposit(
    req: OpenDepositRequest,
    accounts: Dict[str, AccountInfo],
    is_valid_account_name,
) -> DepositPlan:
    """Check the request against the existing ``accounts`` and return what to
    write. Raises ``DepositError`` for anything that can't be written."""
    name = req.account.strip()
    if not is_valid_account_name(name) or _root(name) != "Assets":
        raise DepositError(ec.INVALID_ACCOUNT_FORMAT,
                           "The deposit account must be a valid Assets account name, e.g. Assets:Bank:CD-2026.",
                           details={"field": "account"})
    if name in accounts:
        raise DepositError(ec.ACCOUNT_ALREADY_EXISTS, "This account already exists.", 409,
                           details={"field": "account", "account": name})

    if req.maturity_date is not None:
        if req.account_type == "savings":
            raise DepositError(ec.VALIDATION_ERROR, "A savings account has no maturity date.",
                               details={"field": "maturity_date"})
        if req.maturity_date <= req.open_date:
            raise DepositError(ec.VALIDATION_ERROR, "The maturity date must be after the open date.",
                               details={"field": "maturity_date"})

    warnings: List[str] = []
    deposit_meta: Dict[str, Any] = {"account-type": req.account_type}
    if req.interest_rate and req.interest_rate.strip():
        deposit_meta["interest_rate"] = req.interest_rate.strip()
    if req.maturity_date is not None:
        deposit_meta["maturity_date"] = req.maturity_date

    opens: List[OpenSpec] = []
    created_income: Optional[OpenSpec] = None
    inc = req.income_account
    if inc is not None:
        inc_name = inc.account.strip()
        if inc.mode == "new":
            if not is_valid_account_name(inc_name) or _root(inc_name) != "Income":
                raise DepositError(ec.INVALID_ACCOUNT_FORMAT,
                                   "The interest income account must be a valid Income account name.",
                                   details={"field": "income_account"})
            if inc_name in accounts:
                raise DepositError(ec.ACCOUNT_ALREADY_EXISTS,
                                   "This income account already exists. Choose it as an existing account instead.",
                                   409, details={"field": "income_account", "account": inc_name})
            deposit_meta["interest_account"] = inc_name
            created_income = OpenSpec(inc_name, req.open_date, [req.currency], {"income-type": "interest"})
        else:
            info = accounts.get(inc_name)
            if info is None or _root(inc_name) != "Income":
                raise DepositError(ec.ACCOUNT_NOT_FOUND, "Choose an existing Income account.", 404,
                                   details={"field": "income_account", "account": inc_name})
            if info.closed:
                raise DepositError(ec.ACCOUNT_ALREADY_CLOSED, "This income account is closed.",
                                   details={"field": "income_account", "account": inc_name})
            # Interest booked to it must be valid: the currency allowed, and open in time.
            if info.currencies and req.currency not in info.currencies:
                raise DepositError(ec.VALIDATION_ERROR, f"{inc_name} doesn't allow {req.currency}.",
                                   details={"field": "income_account", "account": inc_name})
            if info.open_date is not None and info.open_date > req.open_date:
                raise DepositError(ec.VALIDATION_ERROR,
                                   f"{inc_name} is opened on {info.open_date.isoformat()}, after this account.",
                                   details={"field": "income_account", "account": inc_name})
            if str(info.metadata.get("income-type") or "").strip().lower() != "interest":
                warnings.append(f"{inc_name} has no income type of interest, so its interest is not matched to accounts.")
            linked_by = [n for n, a in accounts.items()
                         if str(a.metadata.get("interest_account") or "") == inc_name]
            if linked_by:
                warnings.append(f"{inc_name} is already linked to {linked_by[0]}, so it is not linked to the new account.")
            else:
                deposit_meta["interest_account"] = inc_name

    opens.append(OpenSpec(name, req.open_date, [req.currency], deposit_meta))
    if created_income is not None:
        opens.append(created_income)

    transfer = None
    fd = req.first_deposit
    if fd is not None:
        src = fd.from_account.strip()
        info = accounts.get(src)
        if info is None or _root(src) not in ("Assets", "Liabilities"):
            raise DepositError(ec.ACCOUNT_NOT_FOUND, "Choose an existing Assets or Liabilities account to move the money from.",
                               404, details={"field": "from_account", "account": src})
        if info.closed:
            raise DepositError(ec.ACCOUNT_ALREADY_CLOSED, "The account the money comes from is closed.",
                               details={"field": "from_account", "account": src})
        if info.currencies and req.currency not in info.currencies:
            raise DepositError(ec.VALIDATION_ERROR,
                               f"{src} doesn't allow {req.currency}.",
                               details={"field": "from_account", "account": src})
        when = fd.date or req.open_date
        if when < req.open_date:
            raise DepositError(ec.VALIDATION_ERROR, "The first deposit can't be before the open date.",
                               details={"field": "first_deposit_date"})
        if req.maturity_date is not None and when >= req.maturity_date:
            raise DepositError(ec.VALIDATION_ERROR, "The first deposit must be before the maturity date.",
                               details={"field": "first_deposit_date"})
        # The ledger write check only blocks booking errors, so catch this one here:
        # a posting before an account's open is a ledger error.
        if info.open_date is not None and when < info.open_date:
            raise DepositError(ec.VALIDATION_ERROR,
                               f"{src} is opened on {info.open_date.isoformat()}, after the first deposit date.",
                               details={"field": "first_deposit_date", "account": src})
        transfer = (when, f"Opening deposit to {name}",
                    [(name, fd.amount, req.currency), (src, -fd.amount, req.currency)], src)

    return DepositPlan(opens=opens, transfer=transfer, warnings=warnings)


def preview_hash(preview: str, warnings: List[str]) -> str:
    """Fingerprint of what the user is shown (the text and the warnings), so a
    write can confirm it would write exactly that."""
    return hashlib.sha256((preview + "\0" + "\n".join(warnings)).encode("utf-8")).hexdigest()
