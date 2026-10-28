# Beancount 3.2.0 beancount/core/inventory.py: the names the engine uses (S0049).
from collections.abc import Iterator, Mapping
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.position import Cost, Position

class Inventory(dict[tuple[str, Cost | None], Position]):
    def __iter__(self) -> Iterator[Position]: ...  # type: ignore[override]  # Beancount iterates the positions
    def is_small(self, tolerances: Decimal | Mapping[str, Decimal]) -> bool: ...
    def get_currency_units(self, currency: str) -> Amount: ...
