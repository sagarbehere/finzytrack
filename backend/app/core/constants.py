"""
Centralized Beancount domain constants.
"""

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
