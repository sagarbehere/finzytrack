# Beancount 3.2.0 beancount/core/interpolate.py: the names the engine uses (S0049).
from typing import Any

from beancount.core.data import Posting
from beancount.core.inventory import Inventory
from beancount.utils.defdict import ImmutableDictWithDefault

AUTOMATIC_META: str

def infer_tolerances(
    postings: list[Posting], options_map: dict[str, Any], use_cost: bool | None = None
) -> ImmutableDictWithDefault: ...
def compute_residual(postings: list[Posting]) -> Inventory: ...
