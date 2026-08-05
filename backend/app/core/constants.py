"""
Centralized Beancount domain constants.
"""

from beancount.core.amount import CURRENCY_RE

# What a commodity/currency code may look like — derived from Beancount's own
# lexer rule rather than re-guessed. Beancount permits `.`, `-`, `_` and `'`
# inside a code, so `BRK.B`, `ELEC-DAYS` and `X_1` are all valid; a
# hand-written `^[A-Z0-9]+$` rejected them and made such ledgers unopenable.
#
# Anchored for whole-string validation. Every API-level commodity/currency
# field must use this — do not re-spell the rule at a call site, and mirror it
# in the UI only with a comment pointing back here (see
# frontend/src/components/settings/GeneralSettingsTab.vue).
COMMODITY_CODE_PATTERN = rf"^(?:{CURRENCY_RE})$"

# Beancount imposes no length limit on a code. This bound exists only to keep
# API payloads sane, and is set well above any plausible real code so it can
# never be the thing that rejects a valid ledger.
COMMODITY_CODE_MAX_LENGTH = 64

# The five Beancount account types
ACCOUNT_TYPES = ("Assets", "Liabilities", "Equity", "Income", "Expenses")

# Account type prefixes (with colon) for startswith() checks
ACCOUNT_TYPE_PREFIXES = tuple(f"{t}:" for t in ACCOUNT_TYPES)

# Balance sheet account types (point-in-time balance)
BALANCE_SHEET_TYPES = ("Assets", "Liabilities", "Equity")
BALANCE_SHEET_PREFIXES = tuple(f"{t}:" for t in BALANCE_SHEET_TYPES)

# Income statement account types (period-based balance)
INCOME_STATEMENT_TYPES = ("Income", "Expenses")
INCOME_STATEMENT_PREFIXES = tuple(f"{t}:" for t in INCOME_STATEMENT_TYPES)

# Asset/liability types (source accounts)
SOURCE_ACCOUNT_TYPES = ("Assets", "Liabilities")
SOURCE_ACCOUNT_PREFIXES = tuple(f"{t}:" for t in SOURCE_ACCOUNT_TYPES)
