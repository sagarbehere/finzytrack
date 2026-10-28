# Beancount 3.2.0 beancount/parser/booking.py: the names the engine uses (S0049).
from typing import Any

from beancount.core.data import BeancountError, Directives

def book(
    incomplete_entries: Directives, options_map: dict[str, Any], initial_balances: Any = None
) -> tuple[Directives, list[BeancountError]]: ...
