# Beancount 3.2.0 beancount/loader.py: the names the engine and its tests use (S0049).
from pathlib import Path
from typing import Any

from beancount.core.data import BeancountError, Directives

OptionsMap = Any

def load_file(
    filename: str | Path,
    log_timings: Any = None,
    log_errors: Any = None,
    extra_validations: list[Any] | None = None,
    encoding: str | None = None,
) -> tuple[Directives, list[BeancountError], OptionsMap]: ...
def run_transformations(
    entries: Directives, parse_errors: list[BeancountError], options_map: OptionsMap, log_timings: Any
) -> tuple[Directives, list[BeancountError]]: ...
def aggregate_options_map(options_map: OptionsMap, other_options_map: list[OptionsMap]) -> OptionsMap: ...
def compute_input_hash(filenames: list[str]) -> str: ...

class LoadError:
    source: dict[str, Any]
    message: str
    entry: None
    def __init__(self, source: dict[str, Any], message: str, entry: None = None) -> None: ...
