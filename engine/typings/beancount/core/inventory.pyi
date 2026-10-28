# Beancount 3.2.0 beancount/core/inventory.py: the names the engine uses (S0049, S0050).
from collections.abc import Iterator, Mapping
from decimal import Decimal
from enum import Enum

from beancount.core.amount import Amount
from beancount.core.data import Posting
from beancount.core.position import Cost, Position

class MatchResult(Enum): ...

class Inventory(dict[tuple[str, Cost | None], Position]):
    def __init__(self, positions: list[Position] | None = None) -> None: ...
    def add_position(self, position: Position | Posting) -> tuple[Position | None, MatchResult]: ...
    def __iter__(self) -> Iterator[Position]: ...  # type: ignore[override]  # Beancount iterates the positions
    def is_small(self, tolerances: Decimal | Mapping[str, Decimal]) -> bool: ...
    def get_currency_units(self, currency: str) -> Amount: ...
