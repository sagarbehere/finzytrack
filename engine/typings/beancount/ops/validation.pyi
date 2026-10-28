# Beancount 3.2.0 beancount/ops/validation.py: the names the engine uses (S0049).
from typing import Any

from beancount.core.data import BeancountError, Directives

def validate(
    entries: Directives,
    options_map: dict[str, Any],
    log_timings: Any = None,
    extra_validations: list[Any] | None = None,
) -> list[BeancountError]: ...
