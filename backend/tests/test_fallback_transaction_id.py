"""
Tests for SQLiteExporter._compute_fallback_id_map / _get_transaction_id.

Locks the contract for the legacy-fallback transaction_id used when a
transaction has neither 'id' nor 'transaction_id' meta — typically because
the user imported a hand-edited Beancount ledger.

Contract:
1. Single un-IDed transaction → fallback is its content_hash.
2. Two transactions with identical content (same date, payee, narration,
   source_account, amount) → distinct stable IDs: <hash> and <hash>-1.
3. Reordering surrounding non-duplicate transactions does not change the
   fallback ID of any un-IDed transaction.
4. Transactions that carry 'id' or 'transaction_id' meta are not in the map.
"""

from datetime import date
from decimal import Decimal

from beancount.core import data as bd
from beancount.core.amount import Amount

from app.services.sqlite_exporter import SQLiteExporter


def _txn(narration: str, *, payee: str = "Corner Shop",
         day: int = 29, amount: str = "-100",
         account_source: str = "Assets:Cash",
         account_dst: str = "Expenses:Food",
         meta: dict | None = None) -> bd.Transaction:
    meta = dict(meta) if meta else {}
    meta.setdefault("filename", "<test>")
    meta.setdefault("lineno", 0)
    postings = [
        bd.Posting(account_source, Amount(Decimal(amount), "INR"), None, None, None, None),
        bd.Posting(account_dst, Amount(-Decimal(amount), "INR"), None, None, None, None),
    ]
    return bd.Transaction(
        meta, date(2026, 5, day), "*", payee, narration, frozenset(), frozenset(), postings,
    )


def test_single_unkeyed_transaction_uses_content_hash():
    txns = [_txn("milk")]
    fallback = SQLiteExporter._compute_fallback_id_map(txns)
    assert 0 in fallback
    # content_hash is a non-empty hex string
    assert fallback[0]
    assert "-" not in fallback[0]  # no ordinal suffix on a unique transaction


def test_identical_duplicates_get_distinct_stable_ids():
    """The milk-twice case: same date, payee, narration (empty), source, amount."""
    txns = [_txn(""), _txn("")]
    fallback = SQLiteExporter._compute_fallback_id_map(txns)
    assert fallback[0] != fallback[1]
    # First occurrence is the bare hash; second is hash-1
    assert not fallback[0].endswith("-1")
    assert fallback[1] == f"{fallback[0]}-1"


def test_triple_duplicates_extend_ordinals():
    txns = [_txn(""), _txn(""), _txn("")]
    fallback = SQLiteExporter._compute_fallback_id_map(txns)
    base = fallback[0]
    assert fallback[1] == f"{base}-1"
    assert fallback[2] == f"{base}-2"


def test_reordering_unrelated_transactions_preserves_fallback_ids():
    """Surrounding non-duplicates must not affect an un-IDed transaction's ID."""
    target = _txn("milk")
    other_a = _txn("bread", payee="Bakery")
    other_b = _txn("eggs", payee="Farm")

    order_1 = [other_a, target, other_b]
    order_2 = [other_b, other_a, target]

    map_1 = SQLiteExporter._compute_fallback_id_map(order_1)
    map_2 = SQLiteExporter._compute_fallback_id_map(order_2)

    # target's content_hash is independent of position
    assert map_1[1] == map_2[2]


def test_transactions_with_id_meta_are_omitted():
    txn_with_id = _txn("milk", meta={"id": "01234567-89ab-cdef-0123-456789abcdef"})
    txn_with_legacy_id = _txn("milk", meta={"transaction_id": "legacy-42"})
    txn_unkeyed = _txn("milk")

    fallback = SQLiteExporter._compute_fallback_id_map(
        [txn_with_id, txn_with_legacy_id, txn_unkeyed]
    )
    assert 0 not in fallback
    assert 1 not in fallback
    assert 2 in fallback


def test_get_transaction_id_prefers_meta_id_over_fallback():
    txn = _txn("milk", meta={"id": "real-id"})
    assert SQLiteExporter._get_transaction_id(txn, "some-fallback") == "real-id"


def test_get_transaction_id_prefers_legacy_meta_over_fallback():
    txn = _txn("milk", meta={"transaction_id": "legacy-42"})
    assert SQLiteExporter._get_transaction_id(txn, "some-fallback") == "legacy-42"


def test_get_transaction_id_returns_supplied_fallback_when_no_meta():
    txn = _txn("milk")
    assert SQLiteExporter._get_transaction_id(txn, "fallback-xyz") == "fallback-xyz"
