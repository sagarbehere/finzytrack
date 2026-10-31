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

# The loader's internals the reconciliation loads wrap (`beancount_port.reconcile_load`, `reconcile_parse`; BL-0118):
# `load_file` without its pickle cache.
def _load(
    sources: list[tuple[str, bool]],
    log_timings: Any,
    extra_validations: list[Any] | None,
    encoding: str | None,
) -> tuple[Directives, list[BeancountError], OptionsMap]: ...
def _parse_recursive(
    sources: list[tuple[str, bool]], log_timings: Any, encoding: str | None = None
) -> tuple[Directives, list[BeancountError], OptionsMap]: ...
