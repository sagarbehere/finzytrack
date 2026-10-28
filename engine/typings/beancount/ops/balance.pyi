# Beancount 3.2.0 beancount/ops/balance.py: the names the engine uses (S0049).
from decimal import Decimal
from typing import Any

from beancount.core.data import Balance

def get_balance_tolerance(balance_entry: Balance, options_map: dict[str, Any]) -> Decimal: ...
