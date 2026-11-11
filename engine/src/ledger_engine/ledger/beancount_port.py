"""The single Beancount gateway: the only engine module that imports `beancount` (INV-02; ADR-0024; ADR-0054).

Finzytrack wraps Beancount and never re-implements it (EP-03). Every other engine module reaches Beancount through
the names here: the Beancount types it needs (re-exported, so the rest of the engine can name them), the parser, the
stages of a full load and the semantic calls (tolerance, residual, balance assertions, display precision).

**Three classes of wrapper** (ARCHITECTURE.md §2.4 rule 4; `engine/tests/architecture/test_load_rule.py`):
- `LOAD_STAGES`, the steps of a full load (sort, booking, transformations with plugins, validation, options
  aggregation). Only `ledger/overlay.py` uses them, so every full load is the overlay replica (ADR-0010), with its
  `LoadPolicy` and its refusals.
- `REAL_LOADS`, Beancount's own loader with no pickle cache: the rebuild after a save (`design/specs/engine-jobs.md`
  §6.7; ADR-0068 D1; BL-0118). Only `ledger/rebuild.py` uses them: the screens show Beancount's own result, never
  the replica's.
- `WRAPPERS`, everything else: parser-only calls and the semantic calls, usable anywhere in the engine.
Other modules use only the names in `__all__`; every function there is in exactly one of the three classes.
Beancount's own names are imported under private (`_`) aliases, so none of its load steps is reachable under a public
name.

**No `load_file` or `load_string` here.** Beancount's `load_file` keeps a pickle cache beside the root file: it reads
`.<name>.picklecache` (unpickling a file from the ledger folder runs whatever code it holds), deletes it when stale
and writes a new one after a load of a second or more. The engine never reads or writes beside a ledger outside the
write path (S0049 journal). The real load calls the loader's internal `_load`, which `load_file` wraps
with that cache: they read the ledger files and nothing else (no `.picklecache` is read or written,
`engine/tests/ledger/test_rebuild.py`).

**Module lookups** (S0099, BL-0124): the only engine module that looks modules up by name. While a load runs,
`recording_imports` stands a recorder in for the import system's path search (`importlib.machinery.PathFinder` in
`sys.meta_path`): each answer the import uses is reported first (`ModuleLookup`), so the caller can hash the file it
names before the loader reads it. `find_modules` asks the same questions again later without importing anything (the
save's gate runs no user code, ADR-0068 D1).

Pinned to Beancount 3.2.0 (ADR-0004); `BEANCOUNT_VERSION` is checked by the overlay's pin test.
"""

from __future__ import annotations

import datetime
import decimal
import errno
import importlib.machinery
import io
import sys
import zipimport
from collections.abc import Buffer, Callable, Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import version
from types import ModuleType
from typing import Any, Final, cast, override

import beancount.parser.parser as _parser_module
from beancount.core.amount import Amount as _Amount
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
from beancount.core.interpolate import quantize_with_tolerance as _quantize_with_tolerance
from beancount.core.inventory import Inventory as _Inventory
from beancount.core.number import MISSING as _MISSING
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
from beancount.parser.lexer import lex_iter as _lex_iter
from beancount.parser.options import OPTIONS_DEFAULTS as _OPTIONS_DEFAULTS
from beancount.parser.parser import parse_file as _parse_file
from beancount.parser.parser import parse_string as _parse_string
from beancount.utils import encryption as _encryption
from beancount.utils.encryption import is_encrypted_file as _is_encrypted_file

__all__ = [
    "BEANCOUNT_CONTEXT",
    "BEANCOUNT_VERSION",
    "CUSTOM_VALUE_TYPES",
    "LOAD_STAGES",
    "PARSER_CLASSES",
    "REAL_LOADS",
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
    "ModuleLookup",
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
    "WrittenNumber",
    "aggregate_options",
    "assertion_balance",
    "balance_tolerance",
    "beancount_context",
    "book",
    "booked_positions",
    "check_transaction",
    "default_options",
    "display_precision",
    "fill_weight",
    "filled_amount",
    "find_modules",
    "input_hash",
    "is_encrypted_file",
    "is_interpolated",
    "load_error",
    "parse_file",
    "parse_string",
    "quantize_fill",
    "real_load",
    "recording_imports",
    "run_transformations",
    "sort_entries",
    "sort_order",
    "validate",
    "with_units_number",
    "written_numbers",
]

BEANCOUNT_VERSION: Final[str] = version("beancount")

CUSTOM_VALUE_TYPES: Final[dict[type, str]] = {
    str: "str",
    bool: "bool",
    Decimal: "Decimal",
    _Amount: "Amount",
    datetime.date: "date",
}
"""Every class Beancount 3.2.0's parser records as a `custom` value's type (`grammar.Builder.custom_value`: the
value's own type for a string, a date, a flag, an amount and a number; an account gets the text `account.TYPE`,
not a class), by identity, each with a fixed name (S0095 red team F1: a class is compared only when it is one of
these, never by its name). `engine/tests/ledger/test_loads_same.py` holds it equal to what the parser gives."""

PARSER_CLASSES: Final[dict[type, str]] = {**CUSTOM_VALUE_TYPES, _MISSING: "MISSING"}
"""Every class Beancount itself puts in a load's entries, options or errors as a value: the `custom` value types and
`number.MISSING`, the parser's marker for an amount left out, which stays in an entry whose booking failed (the
parity corpus's `options` case). A copy's comparison knows these classes and no other (S0095)."""

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
REAL_LOADS: Final = frozenset({"real_load"})
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
        "written_numbers",
        "beancount_context",
        "fill_weight",
        "quantize_fill",
        "filled_amount",
        "with_units_number",
        "recording_imports",
        "find_modules",
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


_OPERATORS: Final = frozenset({"PLUS", "MINUS", "ASTERISK", "SLASH", "LPAREN", "RPAREN"})
_OPERANDS: Final = frozenset({"NUMBER", "RPAREN"})


@dataclass(frozen=True, slots=True)
class WrittenNumber:
    """A number as written in a ledger file, read by Beancount's own lexer: its line, its text (thousands separators
    kept; a sign minus included), its exact value as written (the sign applied, so `-x` is the negative number the
    user wrote), and whether it stands alone (`alone`: a signed number, not part of an expression such as `(a + b)`)."""

    lineno: int
    text: str
    value: Decimal
    alone: bool


# @concept number-parsing
def written_numbers(data: bytes) -> Iterator[WrittenNumber]:
    """Each number written in `data`, as Beancount's own lexer reads it (`lexer.lex_iter`, the parser's lexer). A minus
    is a sign when no number or `)` stands before it; strings, comments, dates and names hold no number."""
    tokens = list(_lex_iter(io.BytesIO(data)))
    for i, (kind, lineno, text, value) in enumerate(tokens):
        if kind != "NUMBER" or not isinstance(value, Decimal):
            continue
        before = tokens[i - 1][0] if i > 0 else ""
        signed = before == "MINUS" and (i < 2 or tokens[i - 2][0] not in _OPERANDS)
        first = tokens[i - 2][0] if signed and i > 1 else before
        after = tokens[i + 1][0] if i + 1 < len(tokens) else ""
        alone = (signed or before not in _OPERATORS) and first not in _OPERATORS and after not in _OPERATORS
        shown = ("-" if signed else "") + text.decode("ascii", "replace")
        yield WrittenNumber(lineno, shown, value.copy_negate() if signed else value, alone)


def sort_order(entry: object) -> int:
    """The rank of an entry's type among entries of one date, as Beancount sorts them (`data.SORT_ORDER`: `Open`
    first, then `Balance`, then the rest, `Document`, `Close` last)."""
    return cast("dict[type, int]", _SORT_ORDER).get(type(entry), 0)


def is_encrypted_file(path: str) -> bool:
    """Beancount's own test: a `.gpg` name, or an `.asc` name whose file starts with a PGP message header."""
    return _is_encrypted_file(path)


# ---- the decimal context every load runs in ------------------------------------------------------------------------

BEANCOUNT_CONTEXT: Final = decimal.Context(
    prec=28,
    rounding=decimal.ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
)
"""Python's default decimal context, the one bean-check and Fava run Beancount in (28 significant digits), spelled out
so no plugin can change it: `decimal.DefaultContext` is itself a mutable object a plugin could alter
(`engine/tests/ledger/test_precision.py` holds the two equal)."""


@contextmanager
def beancount_context() -> Generator[None]:
    """A load's own decimal context (S0098 round 1, finance F1): a fresh copy of `BEANCOUNT_CONTEXT` for the current
    thread, the caller's context restored after. A plugin that changes the context (`getcontext().prec = 64`) changes
    it for the rest of its own load, as in bean-check, and for nothing after it."""
    with decimal.localcontext(BEANCOUNT_CONTEXT):
        yield


# ---- module lookups (S0099, BL-0124) -------------------------------------------------------------------------------

_PATH_FINDER: Final = importlib.machinery.PathFinder


@dataclass(frozen=True, slots=True)
class ModuleLookup:
    """One answer of the import system's path search: `name` (the module's full name) searched in the folders `path`,
    in order (`sys.path` for a top-level module, its package's folders for a submodule); `found`; `origin`, the file
    it is loaded from (None for a namespace package); `locations`, a package's folders; `file`, the file whose bytes
    the loader reads (`origin`, or the zip archive holding it; None when there is none)."""

    name: str
    path: tuple[str, ...]
    found: bool
    origin: str | None = None
    locations: tuple[str, ...] | None = None
    file: str | None = None


def _search_path(path: Iterable[object] | None) -> tuple[str, ...]:
    """The folders the path search asks, in order: `sys.path` for a top-level module. Entries that are not strings are
    skipped, as the path search skips them."""
    return tuple(e for e in (sys.path if path is None else path) if isinstance(e, str))


def _lookup(name: str, path: tuple[str, ...], spec: Any) -> ModuleLookup:
    if spec is None:
        return ModuleLookup(name, path, found=False)
    locations = spec.submodule_search_locations
    origin = cast("str | None", spec.origin) if spec.has_location else None
    loader = spec.loader
    file = loader.archive if isinstance(loader, zipimport.zipimporter) else origin
    return ModuleLookup(
        name,
        path,
        found=True,
        origin=origin,
        locations=None if locations is None else tuple(str(p) for p in locations),
        file=file,
    )


class _SourceOnly(importlib.machinery.SourceFileLoader):
    """A module's source loader that never reads cached bytecode (S0099 r1, verifier F1): a `.pyc` in a `__pycache__`
    folder beside a plugin (left by `bean-check`, or compiled from other code) or under a `pycache_prefix` would run
    code the fingerprint does not hash. A read of bytecode fails, so Python compiles from the source, the file hashed;
    every other read goes through, so a package's data (`pkgutil.get_data`, `__loader__.get_data`) reads as under
    Beancount (S0099 r2, verifier F1; owner, 2026-11-10, "Narrow the refusal"). No bytecode is written either,
    whatever the interpreter's flags (INV-30)."""

    @override
    def get_data(self, path: str) -> bytes:
        if _is_bytecode(path):
            raise OSError(errno.ENOENT, "cached bytecode is not read during a load", path)
        return super().get_data(path)

    @override
    def set_data(self, path: str, data: Buffer, *, _mode: int = 0o666) -> None:
        """Never writes."""


def _is_bytecode(path: str) -> bool:
    """Whether a read is of cached bytecode: Python names every bytecode file it reads for a source module, beside it
    (`__pycache__`) or under `pycache_prefix`, with a bytecode suffix (`.pyc`, `importlib.util.cache_from_source`). A
    package data file a plugin names with that suffix is refused too (a limit, spec §5.1)."""
    return path.endswith(tuple(importlib.machinery.BYTECODE_SUFFIXES))


class _Recorder:
    """The path search, recorded: it asks `PathFinder` with the folders it would search and reports the answer before
    returning it, so the record is the answer the import used. A module found as source is loaded from its source
    alone (`_SourceOnly`)."""

    def __init__(self, on_lookup: Callable[[ModuleLookup], None]) -> None:
        self._on_lookup = on_lookup

    def find_spec(self, fullname: str, path: Iterable[object] | None = None, target: ModuleType | None = None) -> Any:
        search = _search_path(path)
        spec = _PATH_FINDER.find_spec(fullname, list(search), target)
        self._on_lookup(_lookup(fullname, search, spec))
        if spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader:
            spec.loader = _SourceOnly(spec.loader.name, spec.loader.path)
        return spec

    def invalidate_caches(self) -> None:
        _PATH_FINDER.invalidate_caches()


@contextmanager
def recording_imports(on_lookup: Callable[[ModuleLookup], None]) -> Generator[None]:
    """While the block runs, every module the path search looks up is reported to `on_lookup` before it is loaded.
    The path search's caches (folder listings kept by modification time) are emptied first, so a listing older than
    the load never answers. The engine runs one job at a time on one thread, so the swap in `sys.meta_path` is safe;
    the path search is put back wherever the recorder then is, in the list the load started with and in the one
    `sys.meta_path` names after it (a plugin may add finders, or replace the list). A plugin that removes the recorder
    leaves its later imports unrecorded (spec §1, "plugin files")."""
    recorder = _Recorder(on_lookup)
    finders: list[object] = sys.meta_path  # pyright: ignore[reportAssignmentType]
    finders[finders.index(_PATH_FINDER)] = recorder
    _PATH_FINDER.invalidate_caches()
    try:
        yield
    finally:
        now: list[object] = sys.meta_path  # pyright: ignore[reportAssignmentType]
        for listed in (finders, now):
            for i, finder in enumerate(listed):
                if finder is recorder:
                    listed[i] = _PATH_FINDER


def find_modules(keys: Iterable[tuple[str, tuple[str, ...]]]) -> list[ModuleLookup]:
    """The path search's answer for each (name, folders) now, from fresh folder listings; nothing is imported or run,
    and no bytecode is written."""
    asked = list(keys)
    if not asked:
        return []
    _PATH_FINDER.invalidate_caches()
    return [_lookup(name, path, _PATH_FINDER.find_spec(name, list(path))) for name, path in asked]


# ---- the real loads (only ledger/rebuild.py; BL-0118)-------------------------------------------------------


class EncryptedFileMet(Exception):
    """A real load reached an encrypted file (`path`) and did not decrypt it (ADR-0059: v0.4.0 reads no
    encrypted file; the overlay refuses them on its own, `overlay.load`). S0066 verifier r1 F2."""

    def __init__(self, path: str) -> None:
        super().__init__("encrypted file not read")
        self.path = path


@contextmanager
def _never_decrypt() -> Generator[None]:
    """Beancount's parser decrypts a file its `is_encrypted_file` names through `encryption.read_encrypted_file`
    (`gpg --decrypt`); for the real loads that call raises `EncryptedFileMet` instead, so no `gpg` runs.
    The engine runs one op at a time on one thread, so swapping the module's function for the call is safe."""
    real = _encryption.read_encrypted_file

    def refuse(filename: str) -> str:
        raise EncryptedFileMet(filename)

    setattr(_encryption, "read_encrypted_file", refuse)  # noqa: B010 (the module's attribute, for this call only)
    try:
        yield
    finally:
        setattr(_encryption, "read_encrypted_file", real)  # noqa: B010


@contextmanager
def _recording(parsed: list[str] | None) -> Generator[None]:
    """While the real load runs, each file Beancount's loader parses is appended to `parsed` (its `parse_file`, the one
    call `loader._parse_recursive` reads a ledger file with), so the caller knows the files read as ledger text:
    `options["include"]` can also hold what a plugin added (S0098 round 2, verifier F1). The same swap as
    `_never_decrypt`; the engine runs one job at a time."""
    if parsed is None:
        yield
        return
    real = _parser_module.parse_file

    def record(filename: str, *args: Any, **kwargs: Any) -> Any:
        parsed.append(filename)
        return real(filename, *args, **kwargs)

    setattr(_parser_module, "parse_file", record)  # noqa: B010 (the module's attribute, for this call only)
    try:
        yield
    finally:
        setattr(_parser_module, "parse_file", real)  # noqa: B010


def real_load(root: str, parsed: list[str] | None = None) -> ParseResult:
    """Beancount's own full load of the ledger rooted at `root` (an absolute path), as `loader.load_file` runs it but
    without its pickle cache (`loader._load`): parse, sort, booking, plugins with the `pythonpath` first on `sys.path`,
    validation, `input_hash`. No `LoadPolicy` is asked (the rebuild loads only a ledger set the preview's overlay load
    accepted, and its fingerprint checks before and after tell whether the disk is still that set); an encrypted file
    met is not decrypted: `EncryptedFileMet` (the overlay's own refusal, which a file added during the load would
    escape). `parsed`, when given, receives each file the loader parsed, in its order, also when the load raises."""
    with beancount_context(), _never_decrypt(), _recording(parsed):
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

    **No file is decrypted** (ADR-0059; S0067), as in the real load: a plugin that decrypts a side file
    meets `EncryptedFileMet` here as it does in the rebuild's load (Beancount turns an exception in a plugin into an
    error; one raised while a plugin is imported escapes, and the overlay refuses the load). Beancount's documents step
    reads the disk, as in Beancount's own load (S0068 removed S0067's listing swap: ADR-0067, spec §15)."""
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


# @concept tolerance
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


def fill_weight(others: Sequence[Posting], currency: str) -> Decimal:
    """The number Beancount fills in for a leg of `currency` that has no cost or price, before quantizing: the negated
    weight of the transaction's other postings in that currency, as `booking_full.interpolate_group` computes it
    (`interpolate.compute_residual`; zero when they sum to nothing). In the caller's decimal context."""
    for position in _compute_residual(list(others)):
        number = position.units.number
        if position.units.currency == currency and position.cost is None and number is not None:
            return -number
    return Decimal(0)


def quantize_fill(others: Sequence[Posting], currency: str, number: Decimal, options: OptionsMap) -> Decimal:
    """`number` quantized as Beancount quantizes a filled-in amount: to the tolerance it infers for `currency` from
    the transaction's postings (`interpolate.infer_tolerances`, `quantize_with_tolerance`; a leg left blank has no
    number, so the other postings infer the same). In the caller's decimal context."""
    return _quantize_with_tolerance(_infer_tolerances(list(others), options), currency, number)


def with_units_number(posting: Posting, number: Decimal) -> Posting:
    """`posting` with `number` in place of its units' number (same currency, cost, price, metadata): a posting as
    written, for comparing with Beancount's (`ledger.precision`)."""
    units = posting.units
    return posting if units is None else posting._replace(units=_Amount(number, units.currency))


def filled_amount(others: Sequence[Posting], currency: str, options: OptionsMap) -> Decimal:
    """What Beancount fills in for such a leg: `quantize_fill` of `fill_weight`. In Beancount's own context it is
    exactly Beancount's number (`engine/tests/ledger/test_precision.py`), so a wider context tells what Beancount's
    28 digits cut (`ledger.precision`)."""
    return quantize_fill(others, currency, fill_weight(others, currency), options)


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
