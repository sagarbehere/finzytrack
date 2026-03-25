"""
Response validation framework for analyst mode.

After the agent loop finishes (model emits text without further tool calls),
ResponseValidator checks the response against ground truth collected from
tool results and emits advisory warnings — never blocking or retrying.

Five rules:
    1. no_tool_use       — model answered without calling any tools
    2. unknown_account   — response mentions accounts not in the ledger
    3. wrong_currency    — response uses currencies not in the ledger
    4. date_out_of_range — response references dates outside the ledger range
    5. amount_mismatch   — response cites amounts far outside query results
"""

import re
from dataclasses import dataclass, field
from typing import Literal


# ── Ground truth ──────────────────────────────────────────────────────────────

@dataclass
class GroundTruth:
    """Facts collected from tool results during the agent loop."""

    # From get_ledger_context
    known_accounts: set[str] = field(default_factory=set)
    known_currencies: set[str] = field(default_factory=set)
    date_range: tuple[str | None, str | None] = (None, None)  # (min_date, max_date)
    default_currency: str | None = None

    # From execute_query results (last query's output)
    last_query_columns: list[str] = field(default_factory=list)
    last_query_rows: list[dict] = field(default_factory=list)

    # Metadata
    tools_called: list[str] = field(default_factory=list)


# ── Rule result ───────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    status: Literal["pass", "warn"]
    rule_name: str
    message: str | None = None
    details: dict | None = None


# ── Rule 1: no_tool_use ───────────────────────────────────────────────────────

def check_no_tool_use(text: str, gt: GroundTruth) -> RuleResult:
    """Warn if the model answered without calling any tools."""
    if not gt.tools_called:
        return RuleResult(
            status="warn",
            rule_name="no_tool_use",
            message=(
                "The model answered without querying your data. "
                "The response may be fabricated."
            ),
        )
    return RuleResult(status="pass", rule_name="no_tool_use")


# ── Rule 2: unknown_account ───────────────────────────────────────────────────

# Matches Beancount account patterns: Foo:Bar, Expenses:Food:Restaurants, etc.
_ACCOUNT_RE = re.compile(r'\b[A-Z][a-z]+(?::[A-Za-z][\w-]*){1,}\b')


def check_unknown_accounts(text: str, gt: GroundTruth) -> RuleResult:
    """Warn if the response mentions accounts not present in the ledger."""
    if not gt.known_accounts:
        return RuleResult(status="pass", rule_name="unknown_account")

    found = set(_ACCOUNT_RE.findall(text))
    unknown = sorted(a for a in found if a not in gt.known_accounts)

    if unknown:
        examples = ", ".join(unknown[:5])
        return RuleResult(
            status="warn",
            rule_name="unknown_account",
            message=f"Response mentions accounts not in your ledger: {examples}",
            details={"unknown_accounts": unknown},
        )
    return RuleResult(status="pass", rule_name="unknown_account")


# ── Rule 3: wrong_currency ────────────────────────────────────────────────────

_SYMBOL_TO_CODE: dict[str, str] = {
    "₹": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}
_SYMBOL_RE = re.compile(r"[₹$€£¥]")
_CURRENCY_CODE_RE = re.compile(
    r"\b(USD|INR|EUR|GBP|JPY|CAD|AUD|CHF|CNY|HKD|SGD)\b"
)


def check_wrong_currency(text: str, gt: GroundTruth) -> RuleResult:
    """Warn if the response uses currency codes/symbols not in the ledger."""
    if not gt.known_currencies:
        return RuleResult(status="pass", rule_name="wrong_currency")

    used: set[str] = set()
    for symbol in _SYMBOL_RE.findall(text):
        code = _SYMBOL_TO_CODE.get(symbol)
        if code:
            used.add(code)
    for code in _CURRENCY_CODE_RE.findall(text):
        used.add(code)

    wrong = sorted(c for c in used if c not in gt.known_currencies)
    if wrong:
        known_str = ", ".join(sorted(gt.known_currencies))
        wrong_str = ", ".join(wrong)
        return RuleResult(
            status="warn",
            rule_name="wrong_currency",
            message=(
                f"Response uses {wrong_str} but your ledger only contains "
                f"{known_str} transactions"
            ),
            details={"wrong_currencies": wrong, "known_currencies": list(gt.known_currencies)},
        )
    return RuleResult(status="pass", rule_name="wrong_currency")


# ── Rule 4: date_out_of_range ─────────────────────────────────────────────────

_MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b"
)
_QUARTER_YEAR_RE = re.compile(r"\b(Q[1-4])\s+(\d{4})\b")
_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")  # restrict to 1900-2099


def _year_in_range(year: int, min_date: str, max_date: str) -> bool:
    min_year = int(min_date[:4])
    max_year = int(max_date[:4])
    return min_year <= year <= max_year


def check_date_out_of_range(text: str, gt: GroundTruth) -> RuleResult:
    """Warn if the response references years outside the ledger's date range."""
    min_date, max_date = gt.date_range
    if not min_date or not max_date:
        return RuleResult(status="pass", rule_name="date_out_of_range")

    out_of_range: list[str] = []

    for m in _MONTH_YEAR_RE.finditer(text):
        year = int(m.group(2))
        if not _year_in_range(year, min_date, max_date):
            ref = m.group(0)
            if ref not in out_of_range:
                out_of_range.append(ref)

    for m in _QUARTER_YEAR_RE.finditer(text):
        year = int(m.group(2))
        if not _year_in_range(year, min_date, max_date):
            ref = m.group(0)
            if ref not in out_of_range:
                out_of_range.append(ref)

    for m in _YEAR_RE.finditer(text):
        year = int(m.group(1))
        if not _year_in_range(year, min_date, max_date):
            ref = m.group(0)
            if ref not in out_of_range:
                out_of_range.append(ref)

    if out_of_range:
        min_pretty = min_date[:7]
        max_pretty = max_date[:7]
        examples = ", ".join(out_of_range[:3])
        return RuleResult(
            status="warn",
            rule_name="date_out_of_range",
            message=(
                f"Response discusses {examples} but your ledger runs from "
                f"{min_pretty} to {max_pretty}"
            ),
            details={"out_of_range": out_of_range, "ledger_range": [min_date, max_date]},
        )
    return RuleResult(status="pass", rule_name="date_out_of_range")


# ── Rule 5: amount_mismatch ───────────────────────────────────────────────────

# Matches currency-prefixed amounts: ₹1,23,456.78 or $1,234.56
_AMOUNT_RE = re.compile(r"[₹$€£¥]\s*[\d,]+(?:\.\d+)?")


def _parse_amount(raw: str) -> float | None:
    cleaned = re.sub(r"[₹$€£¥,\s]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def check_amount_mismatch(text: str, gt: GroundTruth) -> RuleResult:
    """
    Warn if the response mentions amounts far exceeding all query result values.

    Conservative: only flags amounts > 3× the maximum in the result set, with a
    minimum absolute threshold. This catches egregious fabrications while
    tolerating model-computed derived values (sums, percentages, averages).
    """
    if not gt.last_query_rows:
        return RuleResult(status="pass", rule_name="amount_mismatch")

    # Collect all numeric values from query results
    result_values: list[float] = []
    for row in gt.last_query_rows:
        for v in row.values():
            try:
                result_values.append(abs(float(v)))
            except (TypeError, ValueError):
                pass

    if not result_values:
        return RuleResult(status="pass", rule_name="amount_mismatch")

    max_result = max(result_values)

    # Extract currency-prefixed amounts from response text
    fabricated: list[str] = []
    for m in _AMOUNT_RE.finditer(text):
        amount = _parse_amount(m.group(0))
        if amount is None or amount <= 0:
            continue
        # Flag amounts significantly larger than max result (conservative threshold)
        if amount > max_result * 3 and amount > 1000:
            raw = m.group(0).strip()
            if raw not in fabricated:
                fabricated.append(raw)

    if fabricated:
        examples = ", ".join(fabricated[:3])
        return RuleResult(
            status="warn",
            rule_name="amount_mismatch",
            message=(
                f"Response mentions {examples} but this amount doesn't appear "
                f"in the query results"
            ),
            details={"fabricated_amounts": fabricated, "max_query_value": max_result},
        )
    return RuleResult(status="pass", rule_name="amount_mismatch")


# ── Validator ─────────────────────────────────────────────────────────────────

class ResponseValidator:
    _CHECKS = [
        check_no_tool_use,
        check_unknown_accounts,
        check_wrong_currency,
        check_date_out_of_range,
        check_amount_mismatch,
    ]

    def validate(self, text: str, ground_truth: GroundTruth) -> list[RuleResult]:
        """Run all rules and return only non-passing results."""
        results = []
        for check in self._CHECKS:
            result = check(text, ground_truth)
            if result.status != "pass":
                results.append(result)
        return results
