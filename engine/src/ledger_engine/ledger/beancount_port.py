"""The single Beancount gateway: the only engine module that imports `beancount` (INV-02; ADR-0024; ADR-0054).

Finzytrack wraps Beancount and never re-implements it (EP-03). Every other engine module reaches Beancount through
the names here: the Beancount types it needs (re-exported, so the rest of the engine can name them), the parser, the
stages of a full load and the semantic calls (tolerance, residual, balance assertions, display precision).

**Three classes of wrapper** (ARCHITECTURE.md §2.4 rule 4; `engine/tests/architecture/test_load_rule.py`):
- `LOAD_STAGES`, the steps of a full load (sort, booking, transformations with plugins, validation, options
  aggregation). Only `ledger/overlay.py` uses them, so every full load is the overlay replica (ADR-0010), with its
  `LoadPolicy` and its refusals.
- `RECONCILE_LOADS`, Beancount's own loader with no pickle cache: the full load of the re-check after a save (R4,
  `design/specs/engine-jobs.md` §6.7; BL-0118). Only `ledger/r4.py` uses them: R4 compares the replica's
  prediction with Beancount's real loader, so it must not be the replica (it could not catch the replica's own drift).
- `WRAPPERS`, everything else: parser-only calls and the semantic calls, usable anywhere in the engine.
Other modules use only the names in `__all__`; every function there is in exactly one of the three classes.
Beancount's own names are imported under private (`_`) aliases, so none of its load steps is reachable under a public
name.

**No `load_file` or `load_string` here.** Beancount's `load_file` keeps a pickle cache beside the root file: it reads
`.<name>.picklecache` (unpickling a file from the ledger folder runs whatever code it holds), deletes it when stale
and writes a new one after a load of a second or more. The engine never reads or writes beside a ledger outside the
write path (S0049 journal). The reconciliation load calls the loader's internal `_load`, which `load_file` wraps
with that cache: they read the ledger files and nothing else (no `.picklecache` is read or written,
`engine/tests/ledger/test_r4.py`).

Pinned to Beancount 3.2.0 (ADR-0004); `BEANCOUNT_VERSION` is checked by the overlay's pin test.
"""

from __future__ import annotations

import sys
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import version
from typing import Any, Final, cast

from beancount.core.data import SORT_ORDER as _SORT_ORDER
from beancount.core.data import (
    Balance,
    BeancountError,
    Close,
    Commodity,
    Custom,
    Directive,
    Document,
    Event,
    Note,
    Open,
    Pad,
    Posting,
    Price,
    Query,
    Transaction,
)
from beancount.core.data import entry_sortkey as _entry_sortkey
from beancount.core.data import new_metadata as _new_metadata
from beancount.core.display_context import Precision as _Precision
from beancount.core.interpolate import AUTOMATIC_META as _AUTOMATIC_META
from beancount.core.interpolate import compute_residual as _compute_residual
from beancount.core.interpolate import infer_tolerances as _infer_tolerances
from beancount.core.inventory import Inventory as _Inventory
from beancount.core.position import Cost, Position
from beancount.core.realization import compute_balance as _compute_balance
from beancount.core.realization import get as _realization_get
from beancount.core.realization import realize as _realize
from beancount.loader import LoadError as _LoadError
from beancount.loader import _load as _loader_load  # pyright: ignore[reportPrivateUsage] (BL-0118)
from beancount.loader import aggregate_options_map as _aggregate_options_map
from beancount.loader import compute_input_hash as _compute_input_hash
from beancount.loader import run_transformations as _run_transformations
from beancount.ops.balance import get_balance_tolerance as _get_balance_tolerance
from beancount.ops.validation import validate as _validate
from beancount.parser.booking import book as _book
from beancount.parser.options import OPTIONS_DEFAULTS as _OPTIONS_DEFAULTS
from beancount.parser.parser import parse_file as _parse_file
from beancount.parser.parser import parse_string as _parse_string
from beancount.utils import encryption as _encryption
from beancount.utils.encryption import is_encrypted_file as _is_encrypted_file

__all__ = [
    "BEANCOUNT_VERSION",
    "LOAD_STAGES",
    "RECONCILE_LOADS",
    "WRAPPERS",
    "Balance",
    "BeancountError",
    "Close",
    "Commodity",
    "Cost",
    "Custom",
    "Directive",
    "DisplayPrecision",
    "Document",
    "EncryptedFileMet",
    "Event",
    "Note",
    "Open",
    "OptionsMap",
    "Pad",
    "ParseResult",
    "Position",
    "Posting",
    "Price",
    "Query",
    "Transaction",
    "TransactionCheck",
    "aggregate_options",
    "assertion_balance",
    "balance_tolerance",
    "book",
    "booked_positions",
    "check_transaction",
    "default_options",
    "display_precision",
    "input_hash",
    "is_encrypted_file",
    "is_interpolated",
    "load_error",
    "parse_file",
    "parse_string",
    "reconcile_load",
    "run_transformations",
    "sort_entries",
    "sort_order",
    "validate",
]

BEANCOUNT_VERSION: Final[str] = version("beancount")

type OptionsMap = dict[str, Any]
"""Beancount's options map (Beancount types it as `Any`); its keys are Beancount's option names."""

type ParseResult = tuple[list[Directive], list[BeancountError], OptionsMap]

LOAD_STAGES: Final = frozenset(
    {
        "sort_entries",
        "book",
        "run_transformations",
        "validate",
        "aggregate_options",
        "default_options",
        "input_hash",
        "load_error",
    }
)
RECONCILE_LOADS: Final = frozenset({"reconcile_load"})
WRAPPERS: Final = frozenset(
    {
        "sort_order",
        "parse_file",
        "parse_string",
        "is_encrypted_file",
        "check_transaction",
        "balance_tolerance",
        "assertion_balance",
        "booked_positions",
        "display_precision",
        "is_interpolated",
    }
)


# ---- parser-only calls ---------------------------------------------------------------------------------------------


def parse_file(path: str) -> ParseResult:
    """Parse one file from disk, as `loader._parse_recursive` does (no includes followed, nothing booked)."""
    entries, errors, options = _parse_file(path)
    return list(entries), list(errors), dict(options)


def parse_string(data: bytes, report_filename: str | None = None) -> ParseResult:
    """Parse bytes as if they were the file `report_filename` (its name goes into every entry's metadata)."""
    entries, errors, options = _parse_string(data, report_filename)
    return list(entries), list(errors), dict(options)


def sort_order(entry: object) -> int:
    """The rank of an entry's type among entries of one date, as Beancount sorts them (`data.SORT_ORDER`: `Open`
    first, then `Balance`, then the rest, `Document`, `Close` last)."""
    return cast("dict[type, int]", _SORT_ORDER).get(type(entry), 0)


def is_encrypted_file(path: str) -> bool:
    """Beancount's own test: a `.gpg` name, or an `.asc` name whose file starts with a PGP message header."""
    return _is_encrypted_file(path)


# ---- the reconciliation loads (only ledger/r4.py; BL-0118) ---------------------------------------------------------


class EncryptedFileMet(Exception):
    """A reconciliation load reached an encrypted file (`path`) and did not decrypt it (ADR-0059: v0.4.0 reads no
    encrypted file; the overlay refuses them on its own, `overlay.load`). S0066 verifier r1 F2."""

    def __init__(self, path: str) -> None:
        super().__init__("encrypted file not read")
        self.path = path


@contextmanager
def _never_decrypt() -> Generator[None]:
    """Beancount's parser decrypts a file its `is_encrypted_file` names through `encryption.read_encrypted_file`
    (`gpg --decrypt`); for the reconciliation loads that call raises `EncryptedFileMet` instead, so no `gpg` runs.
    The engine runs one op at a time on one thread, so swapping the module's function for the call is safe."""
    real = _encryption.read_encrypted_file

    def refuse(filename: str) -> str:
        raise EncryptedFileMet(filename)

    setattr(_encryption, "read_encrypted_file", refuse)  # noqa: B010 (the module's attribute, for this call only)
    try:
        yield
    finally:
        setattr(_encryption, "read_encrypted_file", real)  # noqa: B010


def reconcile_load(root: str) -> ParseResult:
    """Beancount's own full load of the ledger rooted at `root` (an absolute path), as `loader.load_file` runs it but
    without its pickle cache (`loader._load`): parse, sort, booking, plugins with the `pythonpath` first on `sys.path`,
    validation, `input_hash`. No `LoadPolicy` is asked (R4 loads only a ledger set the save's overlay load accepted,
    and its fingerprint checks before and after tell whether the disk is still that set); an encrypted file met is
    not decrypted: `EncryptedFileMet` (the overlay's own refusal, which a file added during the load would escape)."""
    with _never_decrypt():
        entries, errors, options = _loader_load([(root, True)], None, None, None)
    return list(entries), list(errors), dict(options)


# ---- the stages of a full load (only ledger/overlay.py) ------------------------------------------------------------


def sort_entries(entries: list[Directive]) -> None:
    entries.sort(key=_entry_sortkey)


def book(entries: list[Directive], options: OptionsMap) -> tuple[list[Directive], list[BeancountError]]:
    booked, errors = _book(entries, options)
    return list(booked), list(errors)


def run_transformations(
    entries: list[Directive], errors: list[BeancountError], options: OptionsMap
) -> tuple[list[Directive], list[BeancountError]]:
    """Beancount's plugins, with the `pythonpath` directories first on `sys.path` while they run, as
    `loader._load` does. `sys.path` is the process's; the engine runs one job at a time (ADR-0064).

    **No file is decrypted** (ADR-0059; S0067), as in the reconciliation load: a plugin that decrypts a side file
    meets `EncryptedFileMet` here as it does in R4's load (Beancount turns an exception in a plugin into an error; one
    raised while a plugin is imported escapes, and the overlay refuses the load). Beancount's documents step reads the
    disk, as in Beancount's own load (S0068 removed S0067's listing swap: ADR-0067, spec §15)."""
    saved = list(sys.path)
    try:
        if "pythonpath" in options:
            sys.path[0:0] = options["pythonpath"]
        with _never_decrypt():
            transformed, all_errors = _run_transformations(entries, errors, options, None)
    finally:
        sys.path[:] = saved
    return list(transformed), list(all_errors)


def validate(entries: list[Directive], options: OptionsMap) -> list[BeancountError]:
    return list(_validate(entries, options, None, None))


def aggregate_options(top: OptionsMap, others: list[OptionsMap]) -> OptionsMap:
    return dict(_aggregate_options_map(top, others))


def default_options() -> OptionsMap:
    return dict(_OPTIONS_DEFAULTS)


def input_hash(paths: list[str]) -> str:
    return _compute_input_hash(paths)


def load_error(message: str) -> BeancountError:
    """An error of the loader itself (a missing file, a duplicate include), as `_parse_recursive` makes it."""
    return _LoadError(_new_metadata("<load>", 0), message)


# ---- semantic calls ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransactionCheck:
    """Beancount's balance check of one booked transaction (`validate_check_transaction_balances`)."""

    tolerances: dict[str, Decimal]
    default: Decimal
    residual: dict[str, Decimal]
    balanced: bool


def check_transaction(postings: Sequence[Posting], options: OptionsMap) -> TransactionCheck:
    """The inferred tolerances and the residual of booked postings, and whether the residual is within them."""
    tolerances = _infer_tolerances(list(postings), options)
    residual = _compute_residual(list(postings))
    amounts = {p.units.currency: p.units.number for p in residual if p.units.number is not None}
    return TransactionCheck(
        tolerances=dict(tolerances),
        default=tolerances.default,
        residual=dict(sorted(amounts.items())),
        balanced=residual.is_small(tolerances),
    )


def balance_tolerance(assertion: Balance, options: OptionsMap) -> Decimal:
    """The tolerance Beancount allows a balance assertion: its explicit `~`, else twice the multiplier at its
    written precision, else 0."""
    return _get_balance_tolerance(assertion, options)


def assertion_balance(entries: Sequence[Directive], assertion: Balance) -> Decimal:
    """The units of the assertion's currency in its account and every child, from the entries before it in
    Beancount's order (a balance assertion is checked at the start of its day).

    One realization per call, so all assertions of a ledger cost assertions x entries. The mirror does not call it:
    its `balance_assertions` rows read Beancount's own verdict, set on each failing assertion by the load (S0050).
    TODO(BL-0119): one pass for every assertion before a whole-ledger caller (the reconciliation messages) uses this.
    """
    index = next(i for i, e in enumerate(entries) if e is assertion)
    account = _realization_get(_realize(list(entries[:index])), assertion.account)
    if account is None:
        return Decimal(0)
    number = _compute_balance(account).get_currency_units(assertion.amount.currency).number
    return Decimal(0) if number is None else number


def booked_positions(transactions: Iterable[Transaction]) -> dict[str, list[Position]]:
    """Per account, the positions Beancount's inventory holds after every posting of `transactions`, added in the
    order given: `Inventory.add_position`, as `realization` builds an account's balance (a lot is merged with an
    equal one, reduced by a matching reduction, and gone when it reaches zero)."""
    inventories: dict[str, _Inventory] = {}
    for transaction in transactions:
        for posting in transaction.postings:
            inventories.setdefault(posting.account, _Inventory()).add_position(posting)
    return {account: list(inventory.values()) for account, inventory in inventories.items()}


@dataclass(frozen=True, slots=True)
class DisplayPrecision:
    """Fractional digits Beancount saw for a currency: the most common and the maximum (ADR-0023)."""

    most_common: int | None
    maximum: int | None


def display_precision(options: OptionsMap, currency: str) -> DisplayPrecision | None:
    """From the load's `dcontext`; None for a currency Beancount never saw a number in."""
    contexts = options["dcontext"].ccontexts
    context = contexts.get(currency)  # .get: the mapping is a defaultdict; indexing would add the currency
    if context is None:
        return None
    return DisplayPrecision(context.get_fractional(_Precision.MOST_COMMON), context.get_fractional(_Precision.MAXIMUM))


def is_interpolated(posting: Posting) -> bool:
    """True for a leg Beancount filled in (an elided amount)."""
    meta = posting.meta
    return meta is not None and _AUTOMATIC_META in meta
