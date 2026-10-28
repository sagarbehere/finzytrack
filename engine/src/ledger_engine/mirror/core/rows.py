"""The mirror's rows from one load result (spec design/specs/mirror/README.md and surface.toml; ADR-0015). Pure.

Each table's rows are a projection of what Beancount loaded (the booked, transformed and validated entries, their
errors and the options map); nothing here re-implements a Beancount rule (README "Traps"; ADR-0024): the balance
verdict and difference are the ones Beancount's balance check set on the entry, the pad pairing is read from the
padding transactions Beancount inserted, lots are Beancount's inventory. What the mirror computes itself is written
in the spec: the TransactionId and content hash (README "Ids"; ADR-0026, ADR-0055, INV-21), `source_account`
(ADR-0055), the metadata JSON (README "Metadata JSON"), and three aggregates in a 64-digit context that refuses to
round (README "Exact numbers": `account_balances.balance`, `lots.book_value`, `commodity_usage.total_volume`).

`today` (the build date in the user's time zone) is the caller's (EP-10, INV-07): `account_balances` and `lots`
stop at it (ADR-0025 amendment). The stamp (`ledger_version`, `fingerprint`, `built_at`) is the caller's too: the
workspace format and the publish own them (BL-0113).
"""

from __future__ import annotations

import decimal
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, cast

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import grammar
from ledger_engine.mirror.core import schema

type Value = str | int | None
type Row = tuple[Value, ...]

# README "Exact numbers": 64 significant digits; a result that would need rounding raises instead of rounding.
EXACT: Final = decimal.Context(
    prec=64, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow, decimal.DivisionByZero]
)
SOURCE_PREFIXES: Final = ("Assets:", "Liabilities:")  # v0.2.2's literal text (README "Ids")
SOURCE_ROOTS: Final = ("Assets", "Liabilities")  # v0.2.2's `_compute_source_account` (README "`source_account`")
CATEGORY_KINDS: Final = ("Income", "Expenses")
KINDS: Final = ("Assets", "Liabilities", "Equity", "Income", "Expenses")


@dataclass(frozen=True, slots=True)
class Stamp:
    """The facts about this mirror that the caller owns (surface.toml `meta`)."""

    ledger_version: str
    fingerprint: str
    built_at: str


class InexactError(Exception):
    """A sum or product the mirror computes would need rounding: the build fails (README "Exact numbers")."""

    def __init__(self, table: str, column: str) -> None:
        super().__init__(f"{table}.{column}")
        self.table = table
        self.column = column


# ---- conversions (README "Exact numbers", "Metadata JSON") ------------------------------------------------------


def number(value: Decimal | None) -> str | None:
    """Plain positional notation with the scale the number has (`format(d, "f")`, never `1E-8`)."""
    return None if value is None else format(value, "f")


def iso(value: date | None) -> str | None:
    """A date as ISO text. Typed optional: a plugin may hand over a lot without a date, which is written as NULL."""
    return None if value is None else value.isoformat()


def json_value(value: Any) -> Any:
    """README "Metadata JSON": a Decimal is a string, a date an ISO string, a tuple (an Amount, a Beancount entry)
    and a set arrays (a set's elements sorted, so the text is the same on every build), anything else not native
    to JSON its Python text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        items = cast("dict[Any, Any]", value)  # metadata keys and values are any Beancount value
        return {k if isinstance(k, str) else str(k): json_value(v) for k, v in items.items()}
    if isinstance(value, set | frozenset):
        return sorted((json_value(v) for v in cast("Iterable[Any]", value)), key=json.dumps)
    if isinstance(value, list | tuple):
        return [json_value(v) for v in cast("Iterable[Any]", value)]
    return str(value)


def meta_json(meta: Mapping[str, Any] | None) -> str | None:
    """A directive's or posting's metadata as JSON; NULL when it has none (v0.2.2)."""
    return json.dumps(json_value(dict(meta))) if meta else None


def tag_list(items: frozenset[str] | None) -> str:
    """A transaction's tags or links: sorted, `[]` when there are none."""
    return json.dumps(sorted(items or ()))


def tag_list_or_null(items: frozenset[str] | None) -> str | None:
    """A note's or document's tags or links: sorted, NULL when there are none."""
    return json.dumps(sorted(items)) if items else None


def meta_text(value: Any) -> str:
    """A metadata value as the ids read it: its Python text."""
    return value if isinstance(value, str) else str(value)


def root(account: str) -> str:
    """The account's first segment as written."""
    return account.split(":", 1)[0]


def kinds(options: port.OptionsMap) -> dict[str, str]:
    """Root name as written -> canonical kind, through the ledger's `name_*` options (ADR-0024)."""
    roots = grammar.account_roots(options)
    names = (roots.assets, roots.liabilities, roots.equity, roots.income, roots.expenses)
    return dict(zip(names, KINDS, strict=True))


def location(meta: Mapping[str, Any] | None) -> tuple[str, int] | None:
    if not meta or meta.get("filename") is None:
        return None
    return str(meta["filename"]), int(meta.get("lineno") or 0)


# ---- TransactionId and content hash (README "Ids"; INV-21) -------------------------------------------------------


def hash_input(txn: port.Transaction) -> str:
    """v0.2.2's content-hash text: `date|payee|narration|amount|account`."""
    meta = txn.meta or {}
    if "source_account" in meta:
        chosen: Any = meta["source_account"]
    else:
        chosen = next((p.account for p in txn.postings if p.account.startswith(SOURCE_PREFIXES)), None)
    if not chosen:  # absent, empty text, or a value Python treats as false
        chosen = txn.postings[0].account if txn.postings else ""
    amount = "0"
    if isinstance(chosen, str):  # text against text
        for p in txn.postings:
            if p.account == chosen and p.units is not None:
                amount = f"{p.units.number} {p.units.currency}"  # str(Decimal), not the mirror's notation
                break
    return f"{txn.date.isoformat()}|{txn.payee or ''}|{txn.narration or ''}|{amount}|{meta_text(chosen).strip()}"


def content_hash(txn: port.Transaction) -> str:
    return hashlib.sha256(hash_input(txn).encode("utf-8")).hexdigest()


def first_import_key(txn: port.Transaction) -> str | None:
    """The first element of the first posting `import-key` that has one (a transaction-level key is no identity)."""
    for p in txn.postings:
        value = (p.meta or {}).get("import-key")
        if isinstance(value, str) and value.split():
            return value.split()[0]
    return None


def transaction_ids(txns: Sequence[port.Transaction], hashes: Sequence[str]) -> list[str]:
    """Rule order: legacy `id`, legacy `transaction_id`, `k:` + import key, content hash with a repeat ordinal."""
    seen: Counter[str] = Counter()
    out: list[str] = []
    for txn, digest in zip(txns, hashes, strict=True):
        meta = txn.meta or {}
        if "id" in meta:
            out.append(meta_text(meta["id"]))
        elif "transaction_id" in meta:
            out.append(meta_text(meta["transaction_id"]))
        elif (key := first_import_key(txn)) is not None:
            out.append(f"k:{key}")
        else:
            out.append(digest if seen[digest] == 0 else f"{digest}-{seen[digest]}")
            seen[digest] += 1
    return out


def source_account(txn: port.Transaction, posting: port.Posting) -> str | None:
    """README "`source_account`": the legacy key on every row; else the leg with an `import-key` on every row; else
    the other leg of two; else the first other `Assets`/`Liabilities` leg (literal roots, as v0.2.2); else NULL."""
    meta = txn.meta or {}
    if "source_account" in meta:
        return meta_text(meta["source_account"])
    keyed = next((p for p in txn.postings if p.meta and "import-key" in p.meta), None)
    if keyed is not None:
        return keyed.account
    others = [p for p in txn.postings if p is not posting]
    if len(txn.postings) == 2 and others:
        return others[0].account
    return next((p.account for p in others if root(p.account) in SOURCE_ROOTS), None)


# ---- tables -------------------------------------------------------------------------------------------------------


def posting_rows(txns: Sequence[port.Transaction], kind_of: Mapping[str, str]) -> list[Row]:
    hashes = [content_hash(t) for t in txns]
    ids = transaction_ids(txns, hashes)
    out: list[Row] = []
    for txn, tid, digest in zip(txns, ids, hashes, strict=True):
        meta = txn.meta or {}
        shared = (
            tid,
            digest,
            txn.date.isoformat(),
            txn.flag,
            txn.payee,
            txn.narration or "",
            tag_list(txn.tags),
            tag_list(txn.links),
        )
        txn_json = meta_json(txn.meta)
        documents = sum(1 for k in meta if isinstance(k, str) and k.startswith("document"))  # pyright: ignore[reportUnnecessaryIsInstance]  # a plugin may add any key
        year, month = txn.date.year, txn.date.month
        for posting in txn.postings:
            units, price = posting.units, posting.price
            cost = posting.cost if isinstance(posting.cost, port.Cost) else None
            source = source_account(txn, posting)
            out.append(
                (
                    len(out) + 1,
                    *shared,
                    posting.account,
                    root(posting.account),
                    None if units is None else number(units.number),
                    None if units is None else units.currency,
                    None if cost is None else number(cost.number),
                    None if cost is None else cost.currency,
                    None if cost is None else iso(cost.date),
                    None if price is None else number(price.number),
                    None if price is None else price.currency,
                    source,
                    None if source is None else root(source),
                    txn_json,
                    meta_json(posting.meta),
                    documents,
                    year,
                    month,
                    (month - 1) // 3 + 1,
                    f"{year}-{month:02d}",
                    kind_of.get(root(posting.account)),
                    None if cost is None else cost.label,
                )
            )
    return out


def account_rows(entries: Sequence[port.Directive], kind_of: Mapping[str, str]) -> list[Row]:
    """The earliest `open` of each account (the first in entry order on a tie); its latest `close`."""
    opens: dict[str, tuple[int, port.Open]] = {}
    closes: dict[str, date] = {}
    for index, entry in enumerate(entries):
        if isinstance(entry, port.Open):
            kept = opens.get(entry.account)
            if kept is None or entry.date < kept[1].date:
                opens[entry.account] = (index, entry)
        elif isinstance(entry, port.Close) and (entry.account not in closes or entry.date > closes[entry.account]):
            closes[entry.account] = entry.date
    out: list[Row] = []
    for _, entry in sorted(opens.values(), key=lambda kept: kept[0]):
        close = closes.get(entry.account)
        out.append(
            (
                entry.account,
                entry.date.isoformat(),
                None if close is None else close.isoformat(),
                json.dumps(list(entry.currencies or [])),
                None if entry.booking is None else str(entry.booking.value),
                meta_json(entry.meta),
                kind_of.get(root(entry.account)),
            )
        )
    return out


def pad_pairing(entries: Sequence[port.Directive]) -> dict[tuple[str, int], port.Pad]:
    """README "Pad pairing": each padding transaction (flag `P`) carries its pad's location on the transaction and
    its assertion's location on its postings; assertion location -> pad."""
    pads = {loc: e for e in entries if isinstance(e, port.Pad) and (loc := location(e.meta)) is not None}
    paired: dict[tuple[str, int], port.Pad] = {}
    for entry in entries:
        if not isinstance(entry, port.Transaction) or entry.flag != "P" or not entry.postings:
            continue
        pad = pads.get(location(entry.meta) or ("", -1))
        assertion = location(entry.postings[0].meta)
        if pad is not None and assertion is not None:
            paired.setdefault(assertion, pad)
    return paired


def balance_rows(entries: Sequence[port.Directive]) -> list[Row]:
    paired = pad_pairing(entries)
    out: list[Row] = []
    for entry in entries:
        if not isinstance(entry, port.Balance):
            continue
        diff = entry.diff_amount  # set by Beancount's balance check on a failing assertion, and only then
        pad = paired.get(location(entry.meta) or ("", -1))
        out.append(
            (
                len(out) + 1,
                entry.date.isoformat(),
                entry.account,
                number(entry.amount.number),
                entry.amount.currency,
                number(entry.tolerance),
                1 if diff is None else 0,
                None if diff is None else number(diff.number),
                None if diff is None else diff.currency,
                meta_json(entry.meta),
                None if pad is None else pad.date.isoformat(),
                None if pad is None else pad.source_account,
            )
        )
    return out


def directive_rows(entries: Sequence[port.Directive]) -> dict[str, list[Row]]:
    """The tables with one row per directive of a kind, numbered in entry order."""
    names = ("pad_directives", "prices", "notes", "events", "documents", "custom_directives", "stored_queries")
    out: dict[str, list[Row]] = {name: [] for name in names}
    for e in entries:
        match e:
            case port.Pad():
                table, row = "pad_directives", (e.account, e.source_account)
            case port.Price():
                table, row = "prices", (e.currency, number(e.amount.number), e.amount.currency)
            case port.Note():
                table, row = "notes", (e.account, e.comment, tag_list_or_null(e.tags), tag_list_or_null(e.links))
            case port.Event():
                table, row = "events", (e.type, e.description)
            case port.Document():
                table, row = "documents", (e.account, e.filename, tag_list_or_null(e.tags), tag_list_or_null(e.links))
            case port.Custom():
                table, row = "custom_directives", (e.type, json.dumps([json_value(v) for v in e.values]))
            case port.Query():
                table, row = "stored_queries", (e.name, e.query_string)
            case _:
                continue
        rows = out[table]
        rows.append((len(rows) + 1, e.date.isoformat(), *row, meta_json(e.meta)))
    return out


def option_rows(options: port.OptionsMap) -> list[Row]:
    return [(str(key), json.dumps(json_value(value))) for key, value in options.items()]


def error_rows(errors: Sequence[port.BeancountError]) -> list[Row]:
    out: list[Row] = []
    for error in errors:
        source: Mapping[str, Any] | None = error.source
        filename = None if not source else source.get("filename")
        lineno = None if not source else source.get("lineno")
        entry = error.entry
        out.append(
            (
                len(out) + 1,
                None if filename is None else str(filename),
                None if lineno is None else int(lineno),
                str(error.message),
                None if entry is None else json.dumps(json_value(entry)),
                type(error).__name__,
            )
        )
    return out


def account_balance_rows(txns: Iterable[port.Transaction]) -> list[Row]:
    """Per (account, currency) with a non-zero sum: the sum of the units, the count of postings with units and the
    latest date. `txns` are the transactions up to the build date."""
    totals: dict[tuple[str, str], tuple[Decimal, int, date]] = {}
    for txn in txns:
        for p in txn.postings:
            if p.units is None or p.units.number is None:
                continue
            key = (p.account, p.units.currency)
            total, count, last = totals.get(key, (Decimal(0), 0, txn.date))
            try:
                total = EXACT.add(total, p.units.number)
            except decimal.Inexact as e:
                raise InexactError("account_balances", "balance") from e
            totals[key] = (total, count + 1, max(last, txn.date))
    return [
        (account, currency, number(total), count, last.isoformat())
        for (account, currency), (total, count, last) in sorted(totals.items())
        if total != 0
    ]


def product(units: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """`units x cost` in the exact context (typed optional: a plugin may hand over a lot without a number)."""
    return None if units is None or cost is None else EXACT.multiply(units, cost)


def lot_rows(txns: Iterable[port.Transaction]) -> list[Row]:
    """Beancount's booked positions held at cost, per account, ordered by account, commodity, date, cost and
    label. `txns` are the transactions up to the build date."""
    lots: list[tuple[str, port.Position, port.Cost]] = []
    for account, positions in port.booked_positions(txns).items():
        lots.extend((account, pos, pos.cost) for pos in positions if pos.cost is not None)

    def order(lot: tuple[str, port.Position, port.Cost]) -> tuple[Any, ...]:
        account, pos, cost = lot
        return (account, pos.units.currency, iso(cost.date) or "", cost.number, cost.currency, cost.label or "")

    out: list[Row] = []
    for account, pos, cost in sorted(lots, key=order):
        units = pos.units.number
        try:
            book = product(units, cost.number)
        except decimal.Inexact as e:
            raise InexactError("lots", "book_value") from e
        out.append(
            (
                len(out) + 1,
                account,
                number(units),
                pos.units.currency,
                number(cost.number),
                cost.currency,
                iso(cost.date),
                cost.label,
                number(book),
            )
        )
    return out


def usage_rows(txns: Iterable[port.Transaction]) -> list[Row]:
    """Per commodity used as posting units, over every transaction (future ones included)."""
    usage: dict[str, tuple[int, Decimal, date, date]] = {}
    for txn in txns:
        for p in txn.postings:
            if p.units is None:
                continue
            code = p.units.currency
            count, volume, first, last = usage.get(code, (0, Decimal(0), txn.date, txn.date))
            if p.units.number is not None:
                try:
                    volume = EXACT.add(volume, abs(p.units.number))
                except decimal.Inexact as e:
                    raise InexactError("commodity_usage", "total_volume") from e
            usage[code] = (count + 1, volume, min(first, txn.date), max(last, txn.date))
    return [
        (code, count, number(volume), first.isoformat(), last.isoformat())
        for code, (count, volume, first, last) in sorted(usage.items())
    ]


def training_rows(txns: Iterable[port.Transaction], kind_of: Mapping[str, str]) -> list[Row]:
    """A transaction with a payee and an Income or Expenses posting: payee, memo (or the legacy `ofx_memo`) and
    narration, and that posting's account."""
    out: list[Row] = []
    for txn in txns:
        if not txn.payee:
            continue
        category = next((p.account for p in txn.postings if kind_of.get(root(p.account)) in CATEGORY_KINDS), None)
        if category is None:
            continue
        meta = txn.meta or {}
        memo = meta.get("memo", "") or meta.get("ofx_memo", "")
        parts = [txn.payee, *([meta_text(memo)] if memo else []), *([txn.narration] if txn.narration else [])]
        out.append((len(out) + 1, " ".join(parts).strip(), category))
    return out


def meta_rows(stamp: Stamp) -> list[Row]:
    return [
        ("surface_version", str(schema.SURFACE_VERSION)),
        ("ledger_version", stamp.ledger_version),
        ("fingerprint", stamp.fingerprint),
        ("export_version", schema.EXPORT_VERSION),
        ("built_at", stamp.built_at),
        ("build_complete", "1"),
    ]


def table_rows(
    entries: Sequence[port.Directive],
    errors: Sequence[port.BeancountError],
    options: port.OptionsMap,
    today: date,
    stamp: Stamp,
) -> dict[str, list[Row]]:
    """Every table's rows, keyed by the table's public name. Raises `InexactError` when an aggregate would round."""
    kind_of = kinds(options)
    txns = [e for e in entries if isinstance(e, port.Transaction)]
    up_to_today = [t for t in txns if t.date <= today]
    tables: dict[str, list[Row]] = {
        "postings": posting_rows(txns, kind_of),
        "accounts": account_rows(entries, kind_of),
        # TODO(BL-0120): the commodities rows, once the owner has said which ISO 4217 codes count as cash
        # (design/inbox/20261028-iso-currency-list.md). Until then the mirror is incomplete and is not published.
        "commodities": [],
        "balance_assertions": balance_rows(entries),
        **directive_rows(entries),
        "ledger_options": option_rows(options),
        "account_balances": account_balance_rows(up_to_today),
        "lots": lot_rows(up_to_today),
        "commodity_usage": usage_rows(txns),
        "ledger_errors": error_rows(errors),
        "training_data": training_rows(txns, kind_of),
        "meta": meta_rows(stamp),
    }
    # TODO(BL-0121): the price sidecar's prices after the ledger's (ADR-0030), when the sidecar is built.
    return tables
