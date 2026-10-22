"""Schemas for statement import (CAS today) — dev-docs/cas-import.md §5–§9, §13.

Flow: ``parse`` (file + password → a short-lived token and what the mapping screen
needs) → ``preview`` (token + mapping + corrections → the plan) → ``commit`` (same, plus
the preview's hash → one ledger write). Money and units are ``Decimal`` in Python and
strings on the wire (dev-docs/money-types.md).
"""

from datetime import date as DateType
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# What a row is taken to mean. The importer proposes one; the user may change it.
Interpretation = Literal[
    "buy", "sell", "reinvest", "dividend", "switch_out", "switch_in", "merge_out", "merge_in",
    "tax", "segregation", "reversal", "info", "unsupported",
]
INTERPRETATIONS = (
    "buy", "sell", "reinvest", "dividend", "switch_out", "switch_in", "merge_out", "merge_in",
    "tax", "segregation", "reversal", "info", "unsupported",
)

# What happens to a row in this import.
RowStatus = Literal[
    "import",          # written by this import (as, or as part of, a transaction)
    "already",         # already in the ledger (an earlier import) — skipped
    "before_start",    # before the history start date — folded into the opening holding
    "before_hand",     # on/before the hand-entered holdings kept for this fund — skipped
    "info",            # no units or money effect (address change, …) — not imported
    "unsupported",     # can't be placed — not imported, shows in reconciliation
    "excluded",        # excluded by the user
    "reversed",        # cancelled by a reversal in the same statement — not imported
    "failed",          # couldn't be built (e.g. not enough units to sell) — not imported
]


class StatementIssue(BaseModel):
    code: str = Field(..., description="Machine-readable issue code")
    message: str = Field(..., description="Plain-language explanation")
    security_key: Optional[str] = Field(default=None, description="Fund the issue is about, if any")
    row_id: Optional[str] = Field(default=None, description="Row the issue is about, if any")


# ── Parse ────────────────────────────────────────────────────────────────────

class StatementDistributor(BaseModel):
    key: str = Field(..., description="Distributor key: the ARN code, or DIRECT")
    label: str = Field(..., description="What to show (ARN code, or 'Direct plans')")
    funds: List[str] = Field(..., description="Names of the funds this distributor covers")


class StatementFundSummary(BaseModel):
    security_key: str
    name: str
    institution: str
    close_units: Decimal


class StatementPreselection(BaseModel):
    """Accounts the user chose for the same roles in their last import (from the ledger)."""
    holding_accounts: Dict[str, str] = Field(default_factory=dict)
    cash_fallback_account: Optional[str] = None
    cash_matching_account: Optional[str] = None
    dividend_account: Optional[str] = None
    taxes_account: Optional[str] = None
    gains_account: Optional[str] = None


class StatementParseData(BaseModel):
    token: str = Field(..., description="Refers to the parsed statement in later calls (30 minutes)")
    source: str = Field(..., description="Statement format, e.g. 'cas'")
    variant: str = Field(..., description="'detailed' or 'summary'")
    period_start: DateType
    period_end: DateType
    investors: List[str] = Field(..., description="Investor names shown on the statement (display only)")
    distributors: List[StatementDistributor]
    funds: List[StatementFundSummary]
    row_count: int
    needs_dividend_account: bool
    needs_taxes_account: bool
    needs_gains_account: bool
    needs_cash_account: bool
    ledger_files: List[str] = Field(..., description="Files the import can write to (root first); one entry = no choice")
    preselected: StatementPreselection
    warnings: List[str] = Field(default_factory=list)


# ── Mapping + corrections (preview / commit input) ───────────────────────────

class FundKey(BaseModel):
    """One fund in one folio, as the plan's FundPlan identifies it."""
    folio: str
    security_key: str


class StatementMapping(BaseModel):
    """The user's choices. Every account is one they picked (never proposed)."""
    holding_accounts: Dict[str, str] = Field(default_factory=dict, description="Distributor key → holding account")
    cash_fallback_account: Optional[str] = Field(default=None, description="Cash side when no bank transaction matches")
    cash_matching_account: Optional[str] = Field(default=None, description="Transfer account holding bank transactions to match")
    match_window_days: int = Field(default=7, ge=0, le=31)
    cutoff_date: Optional[DateType] = Field(default=None, description="Before this date, use cash_before_cutoff_account")
    cash_before_cutoff_account: Optional[str] = None
    history_start: Optional[DateType] = Field(default=None, description="Import history from this date; earlier lots become opening holdings")
    dividend_account: Optional[str] = None
    taxes_account: Optional[str] = None
    gains_account: Optional[str] = None
    commodity_codes: Dict[str, str] = Field(default_factory=dict, description="Fund (security key) → commodity code override")
    hand_entries: Dict[str, Literal["replace", "after_latest"]] = Field(
        default_factory=dict, description="Fund → what to do with hand-entered holdings of it",
    )
    target_file: Optional[str] = Field(default=None, description="Ledger file to write to (one of ledger_files); default the root")
    tag_dividend_account: bool = Field(default=False, description="Set income-type: dividend on the dividend account")
    excluded_funds: List[FundKey] = Field(
        default_factory=list, description="Funds not to import (e.g. someone else's): no transactions, balance check or price",
    )


class RowCorrection(BaseModel):
    """A user's fix to one statement row, applied before planning (§13.3)."""
    row_id: str
    exclude: bool = False
    interpretation: Optional[Interpretation] = None
    pair_with: Optional[str] = Field(default=None, description="Row id to pair with; '' = never pair")
    date: Optional[DateType] = None
    units: Optional[Decimal] = None
    amount: Optional[Decimal] = None


class StatementPlanRequest(BaseModel):
    token: str
    mapping: StatementMapping = Field(default_factory=StatementMapping)
    corrections: List[RowCorrection] = Field(default_factory=list)


class StatementCommitRequest(StatementPlanRequest):
    expected_preview_hash: str = Field(..., description="preview_hash of the preview the user saw")


# ── Plan (preview output) ────────────────────────────────────────────────────

class PlannedPosting(BaseModel):
    account: str
    units: Decimal
    currency: str
    cost: Optional[Decimal] = None
    cost_currency: Optional[str] = None
    cost_date: Optional[DateType] = None
    price: Optional[Decimal] = None
    price_currency: Optional[str] = None


class PlannedTransaction(BaseModel):
    index: int
    date: DateType
    narration: str
    payee: str
    kind: str = Field(..., description="buy, sell, switch, merge, reinvest, dividend, opening, reversal, segregation")
    row_ids: List[str]
    postings: List[PlannedPosting]
    cash_matched: Optional[bool] = Field(default=None, description="True: matched to a bank transaction; False: fallback; None: no cash side")


class PlannedRow(BaseModel):
    id: str
    date: DateType
    kind: str = Field(..., description="Statement row type as printed")
    description: str
    amount: Optional[Decimal] = None
    units: Optional[Decimal] = None
    nav: Optional[Decimal] = None
    balance: Optional[Decimal] = Field(default=None, description="Statement running unit balance")
    interpretation: Interpretation
    proposed: Interpretation = Field(..., description="What the importer proposed before any correction")
    reason: Optional[str] = Field(default=None, description="Why the importer reads it this way, when not obvious")
    status: RowStatus
    corrected: bool = False
    pair_id: Optional[str] = None
    pair_fund: Optional[str] = Field(default=None, description="Name of the paired row's fund")
    parent_id: Optional[str] = Field(default=None, description="For a tax row: the row it belongs to")
    transaction: Optional[int] = Field(default=None, description="Index into transactions")


class YearCheck(BaseModel):
    year: int
    counts: Dict[str, int] = Field(..., description="Interpretation → row count")
    end_units: Decimal = Field(..., description="Units in the ledger at year end after this import")
    statement_units: Optional[Decimal] = Field(default=None, description="Statement running balance at year end")
    ok: Optional[bool] = Field(default=None, description="None when the statement has no balance for that year or it can't be compared")


class FundPlan(BaseModel):
    security_key: str
    folio: str
    name: str
    institution: str
    investor: Optional[str] = None
    distributor: str
    holding_account: Optional[str] = None
    commodity: str
    commodity_status: Literal["new", "existing"]
    isin: Optional[str] = None
    amfi: Optional[str] = None
    close_units: Decimal
    ledger_units: Decimal = Field(..., description="Units in the ledger after the import")
    reconciled: bool
    statement_consistent: bool = Field(..., description="The statement's own rows add up to its closing units")
    held: bool
    excluded: bool = Field(default=False, description="The user chose not to import this fund")
    counts: Dict[str, int] = Field(..., description="Status → row count")
    matched: int = 0
    fallback: int = 0
    hand_entries: Optional[int] = Field(default=None, description="Hand-entered transactions found for this fund")
    hand_entries_latest: Optional[DateType] = None
    hand_entries_choice: Optional[str] = None
    years: List[YearCheck]
    rows: List[PlannedRow]


class PlannedCommodity(BaseModel):
    security_key: str
    code: str
    status: Literal["new", "existing"]
    set_metadata: Dict[str, str] = Field(..., description="Metadata keys this import writes (existing keys are never overwritten)")


class PlannedBalance(BaseModel):
    date: DateType
    account: str
    units: Decimal
    commodity: str
    tolerance: Decimal


class PlannedPrice(BaseModel):
    date: DateType
    commodity: str
    price: Decimal
    currency: str


class UnclaimedBankTransaction(BaseModel):
    date: DateType
    amount: Decimal
    narration: str


class StatementPlanSummary(BaseModel):
    period_start: DateType
    period_end: DateType
    first_date: Optional[DateType] = None
    last_date: Optional[DateType] = None
    years: int
    rows: int
    funds: int
    funds_held: int
    transactions: int
    new_commodities: int
    already_imported: int
    matched: int
    fallback: int
    investors: List[str]


class StatementPlanData(BaseModel):
    summary: StatementPlanSummary
    funds: List[FundPlan]
    transactions: List[PlannedTransaction]
    commodities: List[PlannedCommodity]
    balances: List[PlannedBalance]
    prices: List[PlannedPrice]
    removed_hand_entries: int = Field(..., description="Hand-entered transactions this import replaces")
    unclaimed_bank_transactions: List[UnclaimedBankTransaction] = Field(default_factory=list)
    blocking: List[StatementIssue] = Field(..., description="Must be fixed before Register")
    warnings: List[StatementIssue]
    target_file: str
    preview_hash: str


# ── Commit / imports ─────────────────────────────────────────────────────────

class StatementCommitData(BaseModel):
    import_id: str
    transactions: int
    balances: int
    commodities_created: int
    commodities_updated: int
    prices: int
    removed_hand_entries: int
    failed_balance_checks: List[str] = Field(default_factory=list, description="Balance assertions that don't hold after the write")


class StatementImportRecord(BaseModel):
    import_id: str
    date: DateType
    source: str
    period_start: Optional[DateType] = None
    period_end: Optional[DateType] = None
    transactions: int
    file: str


class StatementImportListData(BaseModel):
    imports: List[StatementImportRecord]


class StatementUndoData(BaseModel):
    import_id: str
    removed_transactions: int
    removed_balances: int


class StatementDetectData(BaseModel):
    is_statement: bool = Field(..., description="True when the PDF is a mutual fund CAS that Import statement can read")
