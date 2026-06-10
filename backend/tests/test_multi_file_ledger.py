"""
Multi-file ledger support — specification tests.

Tests verify behavior described in dev-docs/multi-file-ledger.md (the minimal,
Tier-0 design). Each test builds a ledger on disk via the helpers below,
exercises the public API (LedgerManager / load_ledger_checked), and asserts
on observable outcomes — never on internal call sequences (with one
intentional exception for child-files-before-root ordering, which is a
load-bearing crash-safety property).
"""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

import pytest
from beancount.core import data as bd
from beancount.core.data import Transaction

from app.core.backup_manager import BackupManager
from app.core.beancount_engine import BeancountEngine
from app.core.ledger_initializer import LedgerInitializer
from app.core.ledger_loader import load_ledger_checked
from app.core.ledger_manager import LedgerManager


# ───────────────────────── fixture helpers ──────────────────────────────────

ACCOUNTS_BLOCK = """\
option "operating_currency" "USD"

1970-01-01 open Assets:Bank:Checking       USD
1970-01-01 open Assets:Bank:Savings        USD
1970-01-01 open Income:Salary              USD
1970-01-01 open Expenses:Food              USD
1970-01-01 open Expenses:Rent              USD
1970-01-01 open Equity:Opening-Balances    USD

1970-01-01 commodity USD
"""

TXNS_2025_BLOCK = """\
2025-01-15 * "Employer" "January salary"
  Assets:Bank:Checking        4500.00 USD
  Income:Salary              -4500.00 USD

2025-01-16 * "Landlord" "January rent"
  Expenses:Rent               1500.00 USD
  Assets:Bank:Checking       -1500.00 USD

2025-02-15 * "Employer" "February salary"
  Assets:Bank:Checking        4500.00 USD
  Income:Salary              -4500.00 USD
"""

TXNS_2026_BLOCK = """\
2026-01-15 * "Employer" "January 2026 salary"
  Assets:Bank:Checking        4800.00 USD
  Income:Salary              -4800.00 USD

2026-01-20 * "Grocery Store" "Groceries"
  Expenses:Food                 95.00 USD
  Assets:Bank:Checking         -95.00 USD
"""


def build_single_file_ledger(tmp_path: Path) -> Path:
    """Write an all-in-one ledger and return its absolute path."""
    root = tmp_path / "main.beancount"
    root.write_text(ACCOUNTS_BLOCK + "\n" + TXNS_2025_BLOCK + "\n" + TXNS_2026_BLOCK)
    return root


def build_flat_multi_file_ledger(tmp_path: Path) -> Path:
    """Write a multi-file ledger: root + accounts + per-year transactions.

    Layout (everything in tmp_path):
        main.beancount  (root with 3 includes)
        accounts.beancount
        transactions-2025.beancount
        transactions-2026.beancount

    Returns the absolute path to main.beancount.
    """
    (tmp_path / "accounts.beancount").write_text(ACCOUNTS_BLOCK)
    (tmp_path / "transactions-2025.beancount").write_text(TXNS_2025_BLOCK)
    (tmp_path / "transactions-2026.beancount").write_text(TXNS_2026_BLOCK)
    root = tmp_path / "main.beancount"
    root.write_text(
        'include "accounts.beancount"\n'
        'include "transactions-2025.beancount"\n'
        'include "transactions-2026.beancount"\n'
    )
    return root


def build_nested_multi_file_ledger(tmp_path: Path) -> Path:
    """Write a 3-level nested include chain.

    Layout:
        main.beancount   — includes "accounts.beancount", "2025/index.beancount"
        accounts.beancount
        2025/index.beancount   — includes "jan.beancount", "feb.beancount"
        2025/jan.beancount
        2025/feb.beancount
    """
    (tmp_path / "accounts.beancount").write_text(ACCOUNTS_BLOCK)
    (tmp_path / "2025").mkdir()
    (tmp_path / "2025" / "jan.beancount").write_text(
        """\
2025-01-15 * "Employer" "January salary"
  Assets:Bank:Checking        4500.00 USD
  Income:Salary              -4500.00 USD

2025-01-16 * "Landlord" "January rent"
  Expenses:Rent               1500.00 USD
  Assets:Bank:Checking       -1500.00 USD
"""
    )
    (tmp_path / "2025" / "feb.beancount").write_text(
        """\
2025-02-15 * "Employer" "February salary"
  Assets:Bank:Checking        4500.00 USD
  Income:Salary              -4500.00 USD
"""
    )
    (tmp_path / "2025" / "index.beancount").write_text(
        'include "jan.beancount"\ninclude "feb.beancount"\n'
    )
    root = tmp_path / "main.beancount"
    root.write_text(
        'include "accounts.beancount"\ninclude "2025/index.beancount"\n'
    )
    return root


def build_manager(ledger_file: Path, backup_dir: Path) -> LedgerManager:
    """LedgerManager wired to a given ledger root and backup dir."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_manager = BackupManager(backup_dir=backup_dir, retention_count=5)
    initializer = LedgerInitializer(
        ledger_file=str(ledger_file),
        default_currency="USD",
        backup_manager=backup_manager,
    )
    return LedgerManager(
        ledger_file=str(ledger_file),
        backup_manager=backup_manager,
        ledger_initializer=initializer,
    )


# ───────────────────────── entry comparison helpers ─────────────────────────


def _txn_signature(entry: Transaction) -> tuple:
    """Stable, meta-free signature of a transaction for equality testing."""
    postings = tuple(
        (
            p.account,
            str(p.units.number) if p.units else None,
            p.units.currency if p.units else None,
        )
        for p in entry.postings
    )
    return (
        "Transaction",
        entry.date,
        entry.flag,
        entry.payee or "",
        entry.narration or "",
        tuple(sorted(entry.tags or ())),
        tuple(sorted(entry.links or ())),
        postings,
    )


def _entry_signature(entry) -> tuple:
    """Stable, meta-free signature for any entry type we use in fixtures."""
    if isinstance(entry, Transaction):
        return _txn_signature(entry)
    if isinstance(entry, bd.Open):
        return ("Open", entry.date, entry.account, tuple(entry.currencies or ()))
    if isinstance(entry, bd.Close):
        return ("Close", entry.date, entry.account)
    if isinstance(entry, bd.Commodity):
        return ("Commodity", entry.date, entry.currency)
    if isinstance(entry, bd.Balance):
        return ("Balance", entry.date, entry.account, str(entry.amount.number), entry.amount.currency)
    if isinstance(entry, bd.Pad):
        return ("Pad", entry.date, entry.account, entry.source_account)
    if isinstance(entry, bd.Custom):
        return ("Custom", entry.date, entry.type, tuple(str(v) for v in (entry.values or ())))
    # Fall back to class name + date so anything we don't model is at least counted
    return (entry.__class__.__name__, getattr(entry, "date", None))


def entries_signature_set(entries: Iterable) -> set:
    return {_entry_signature(e) for e in entries}


# ───────────────────────── tests ────────────────────────────────────────────


class TestReadSideMultiFileEquivalence:
    """The flat list of entries returned for a multi-file ledger must be
    identical (modulo meta) to the entries returned for a single-file ledger
    that concatenates the same content."""

    def test_flat_multi_file_parses_to_same_entries_as_single_file(self, tmp_path: Path):
        single_dir = tmp_path / "single"
        single_dir.mkdir()
        single_root = build_single_file_ledger(single_dir)
        multi_dir = tmp_path / "multi"
        multi_dir.mkdir()
        multi_root = build_flat_multi_file_ledger(multi_dir)

        single_entries, single_errors, _ = load_ledger_checked(single_root)
        multi_entries, multi_errors, _ = load_ledger_checked(multi_root)

        assert single_errors == [], f"single-file parse had errors: {single_errors}"
        assert multi_errors == [], f"multi-file parse had errors: {multi_errors}"

        assert entries_signature_set(single_entries) == entries_signature_set(multi_entries)
        assert len(single_entries) == len(multi_entries)

    def test_nested_multi_file_parses_to_same_entries_as_single_file(self, tmp_path: Path):
        single_dir = tmp_path / "single"
        single_dir.mkdir()
        single_root = build_single_file_ledger(single_dir)

        multi_dir = tmp_path / "multi"
        multi_dir.mkdir()
        # Build nested fixture but only with 2025 transactions for parity
        # with single-file (which has 2025 + 2026). So rewrite the single
        # fixture to only contain accounts + 2025 to compare apples to apples.
        single_root.write_text(ACCOUNTS_BLOCK + "\n" + TXNS_2025_BLOCK)
        multi_root = build_nested_multi_file_ledger(multi_dir)

        single_entries, single_errors, _ = load_ledger_checked(single_root)
        multi_entries, multi_errors, _ = load_ledger_checked(multi_root)

        assert single_errors == [], f"single parse errors: {single_errors}"
        assert multi_errors == [], f"multi parse errors: {multi_errors}"

        assert entries_signature_set(single_entries) == entries_signature_set(multi_entries)
        assert len(single_entries) == len(multi_entries)

    def test_multi_file_entries_carry_source_filename(self, tmp_path: Path):
        """Each entry must be stamped with the absolute path of the file
        whose text it came from. This is the invariant the write-path
        grouping logic depends on."""
        multi_dir = tmp_path / "multi"
        multi_dir.mkdir()
        root = build_flat_multi_file_ledger(multi_dir)
        entries, errors, _ = load_ledger_checked(root)
        assert errors == []

        # Bucket each transaction by its source filename.
        by_file: dict[str, list[Transaction]] = {}
        for e in entries:
            if isinstance(e, Transaction):
                fname = (e.meta or {}).get("filename")
                by_file.setdefault(fname, []).append(e)

        txns_2025_path = str(multi_dir / "transactions-2025.beancount")
        txns_2026_path = str(multi_dir / "transactions-2026.beancount")

        # Resolve real paths since the parser may canonicalize.
        def _norm(p: str | None) -> str:
            return str(Path(p).resolve()) if p else ""

        normalized = {_norm(k): v for k, v in by_file.items()}

        # The three 2025 transactions are stamped with the 2025 file.
        assert len(normalized.get(_norm(txns_2025_path), [])) == 3
        # The two 2026 transactions are stamped with the 2026 file.
        assert len(normalized.get(_norm(txns_2026_path), [])) == 2

    def test_discover_includes_per_file_flat(self, tmp_path: Path):
        from app.core.ledger_loader import discover_includes_per_file
        root = build_flat_multi_file_ledger(tmp_path)
        result = discover_includes_per_file(root)

        root_abs = str(root.resolve())
        assert root_abs in result
        included = result[root_abs]
        expected = {
            str((tmp_path / "accounts.beancount").resolve()),
            str((tmp_path / "transactions-2025.beancount").resolve()),
            str((tmp_path / "transactions-2026.beancount").resolve()),
        }
        assert set(included) == expected
        # Child files have no includes of their own.
        for child in expected:
            assert result.get(child) == []

    def test_discover_includes_per_file_nested(self, tmp_path: Path):
        from app.core.ledger_loader import discover_includes_per_file
        root = build_nested_multi_file_ledger(tmp_path)
        result = discover_includes_per_file(root)

        root_abs = str(root.resolve())
        index_abs = str((tmp_path / "2025" / "index.beancount").resolve())
        jan_abs = str((tmp_path / "2025" / "jan.beancount").resolve())
        feb_abs = str((tmp_path / "2025" / "feb.beancount").resolve())
        accts_abs = str((tmp_path / "accounts.beancount").resolve())

        assert set(result[root_abs]) == {accts_abs, index_abs}
        assert set(result[index_abs]) == {jan_abs, feb_abs}
        assert result[jan_abs] == []
        assert result[feb_abs] == []
        assert result[accts_abs] == []


class TestEngineEmitters:
    """Unit tests for format_includes, format_options, format_plugins."""

    def test_format_includes_emits_paths_relative_to_directory(self, tmp_path: Path):
        engine = BeancountEngine()
        (tmp_path / "child.beancount").write_text("")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.beancount").write_text("")

        out = engine.format_includes(
            [
                str((tmp_path / "child.beancount").resolve()),
                str((tmp_path / "sub" / "nested.beancount").resolve()),
            ],
            relative_to=tmp_path,
        )
        assert 'include "child.beancount"' in out
        assert 'include "sub/nested.beancount"' in out

    def test_format_includes_falls_back_to_absolute_when_outside_base(self, tmp_path: Path):
        engine = BeancountEngine()
        outside = tmp_path.parent / f"outside_{tmp_path.name}.beancount"
        outside.write_text("")
        try:
            out = engine.format_includes([str(outside.resolve())], relative_to=tmp_path)
            assert str(outside.resolve()) in out
        finally:
            outside.unlink()

    def test_format_plugins_emits_two_arg_and_one_arg(self):
        engine = BeancountEngine()
        out = engine.format_plugins({
            'plugin': [
                ('beancount.plugins.auto_accounts', None),
                ('beancount.plugins.implicit_prices', 'some config'),
            ],
        })
        assert 'plugin "beancount.plugins.auto_accounts"\n' in out
        assert 'plugin "beancount.plugins.implicit_prices" "some config"\n' in out

    def test_format_options_skips_defaults(self):
        from beancount.parser import parser as bc_parser
        engine = BeancountEngine()
        _, _, opts = bc_parser.parse_string("")
        # No options set ⇒ no lines emitted.
        assert engine.format_options(opts) == ""

    def test_format_options_emits_non_default_scalars(self):
        from beancount.parser import parser as bc_parser
        engine = BeancountEngine()
        _, _, opts = bc_parser.parse_string(
            'option "title" "My Books"\n'
            'option "name_assets" "Aktiva"\n'
        )
        out = engine.format_options(opts)
        assert 'option "title" "My Books"\n' in out
        assert 'option "name_assets" "Aktiva"\n' in out

    def test_format_options_emits_list_one_line_per_element(self):
        from beancount.parser import parser as bc_parser
        engine = BeancountEngine()
        _, _, opts = bc_parser.parse_string(
            'option "operating_currency" "USD"\n'
            'option "operating_currency" "EUR"\n'
        )
        out = engine.format_options(opts)
        assert 'option "operating_currency" "USD"\n' in out
        assert 'option "operating_currency" "EUR"\n' in out

    def test_format_options_emits_bool_as_truefalse(self):
        from beancount.parser import parser as bc_parser
        engine = BeancountEngine()
        _, _, opts = bc_parser.parse_string('option "render_commas" "TRUE"\n')
        out = engine.format_options(opts)
        assert 'option "render_commas" "TRUE"\n' in out

    def test_format_options_round_trip_through_parser(self):
        """Whatever format_options emits must be parseable back to the same value."""
        from beancount.parser import parser as bc_parser
        engine = BeancountEngine()
        src = (
            'option "title" "Round Trip"\n'
            'option "operating_currency" "USD"\n'
            'option "render_commas" "TRUE"\n'
        )
        _, _, opts1 = bc_parser.parse_string(src)
        out = engine.format_options(opts1)
        _, errs, opts2 = bc_parser.parse_string(out)
        assert errs == []
        assert opts2['title'] == opts1['title']
        assert opts2['operating_currency'] == opts1['operating_currency']
        assert opts2['render_commas'] == opts1['render_commas']


    def test_multi_file_open_directives_stamped_with_accounts_file(self, tmp_path: Path):
        multi_dir = tmp_path / "multi"
        multi_dir.mkdir()
        root = build_flat_multi_file_ledger(multi_dir)
        entries, _, _ = load_ledger_checked(root)

        accounts_path = str((multi_dir / "accounts.beancount").resolve())
        opens_in_accounts = [
            e for e in entries
            if isinstance(e, bd.Open)
            and str(Path((e.meta or {}).get("filename", "")).resolve()) == accounts_path
        ]
        # The fixture defines 6 Open directives in the accounts block.
        assert len(opens_in_accounts) == 6


# ───────────────────────── write-path tests ─────────────────────────────────

def _find_txn(entries, narration_substring: str) -> Transaction:
    for e in entries:
        if isinstance(e, Transaction) and narration_substring in (e.narration or ""):
            return e
    raise AssertionError(f"transaction with narration containing {narration_substring!r} not found")


def _backups_for(backup_dir: Path, file_path: Path) -> list[Path]:
    return list(backup_dir.glob(f"{file_path.name}.*.backup"))


def _persist_ids_for_all_txns(mgr: LedgerManager) -> None:
    """Stamp every transaction with id/content_hash meta and write back.

    Production code expects transactions to carry stable IDs in their meta
    before they can be addressed via `update_transactions_by_id`. The
    fixtures we build don't carry IDs, so each test that wants to mutate an
    existing transaction first calls this helper to install IDs throughout
    the ledger (a write that goes through the new multi-file path).
    """
    entries, errors, options = mgr._parse_ledger()
    stamped = []
    for e in entries:
        if isinstance(e, Transaction):
            # Skip auto-generated padding transactions — stamping them with
            # an ID would defeat the format_entries padding filter, which
            # drops 'P'-flag entries that lack a stable ID.
            if e.flag == 'P':
                stamped.append(e)
                continue
            stamped.append(mgr.add_ids_to_transaction(e))
        else:
            stamped.append(e)
    mgr._write_and_export(stamped, errors, options)


def _normalize_ledger(mgr: LedgerManager) -> None:
    """Trigger a no-op write so that every file in the ledger is rewritten
    in the engine's printer format. After this, subsequent writes that
    don't change entries in a given file produce byte-identical content
    (because the existing-content-equality check in _do_write_entries
    skips them)."""
    entries, errors, options = mgr._parse_ledger()
    mgr._write_and_export(entries, errors, options)


def _entries_in_file(mgr: LedgerManager, file_path: Path) -> list:
    """Return all parsed entries whose meta['filename'] is the given file."""
    entries, _, _ = mgr._parse_ledger()
    target = str(file_path.resolve())
    return [
        e for e in entries
        if str(Path((e.meta or {}).get('filename', '')).resolve()) == target
    ]


def _clear_backups(backup_dir: Path) -> None:
    """Delete any backups created by the ID-persistence pre-pass so that
    each test's backup assertions only see the backups its own write creates."""
    if backup_dir.exists():
        for f in backup_dir.glob("*.backup"):
            f.unlink()


class TestRoundTripEdits:
    """Editing an entry must rewrite only its source file."""

    def test_edit_transaction_rewrites_only_its_source_file(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _persist_ids_for_all_txns(mgr)
        _clear_backups(backup_dir)

        # Snapshot byte contents of every file post-ID-persistence
        snapshot = {p.name: p.read_bytes() for p in ledger_dir.glob("*.beancount")}

        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        edited = target._replace(narration="January salary (edited)")
        mgr.update_transactions_by_id([(target.meta['id'], edited)])

        new_2025 = (ledger_dir / "transactions-2025.beancount").read_bytes()
        assert new_2025 != snapshot["transactions-2025.beancount"]
        assert b"January salary (edited)" in new_2025

        assert (ledger_dir / "transactions-2026.beancount").read_bytes() == snapshot["transactions-2026.beancount"]
        assert (ledger_dir / "accounts.beancount").read_bytes() == snapshot["accounts.beancount"]

    def test_edit_in_one_child_does_not_create_backup_for_other_child(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _persist_ids_for_all_txns(mgr)
        _clear_backups(backup_dir)

        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        edited = target._replace(narration="January salary (edited)")
        mgr.update_transactions_by_id([(target.meta['id'], edited)])

        assert len(_backups_for(backup_dir, ledger_dir / "transactions-2025.beancount")) == 1
        assert _backups_for(backup_dir, ledger_dir / "transactions-2026.beancount") == []
        assert _backups_for(backup_dir, ledger_dir / "accounts.beancount") == []

    def test_edit_in_two_children_backs_up_both(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _persist_ids_for_all_txns(mgr)
        _clear_backups(backup_dir)

        entries, _, _ = mgr._parse_ledger()
        t2025 = _find_txn(entries, "January salary")
        t2026 = _find_txn(entries, "January 2026 salary")

        mgr.update_transactions_by_id([
            (t2025.meta['id'], t2025._replace(narration="2025 edit")),
            (t2026.meta['id'], t2026._replace(narration="2026 edit")),
        ])

        assert len(_backups_for(backup_dir, ledger_dir / "transactions-2025.beancount")) == 1
        assert len(_backups_for(backup_dir, ledger_dir / "transactions-2026.beancount")) == 1

    def test_edit_in_nested_file_preserves_intermediate_includes(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_nested_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)

        entries_before, _, _ = mgr._parse_ledger()
        target = _find_txn(entries_before, "January salary")
        edited = target._replace(narration="January salary (edited)")
        mgr.update_transactions_by_id([(target.meta['id'], edited)])

        index_text = (ledger_dir / "2025" / "index.beancount").read_text()
        assert 'include "jan.beancount"' in index_text
        assert 'include "feb.beancount"' in index_text

        entries_after, errors_after, _ = mgr._parse_ledger()
        assert errors_after == []
        assert len(entries_after) == len(entries_before)

    def test_delete_last_entry_leaves_empty_child_file(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)

        entries, _, _ = mgr._parse_ledger()
        txns_2026_path = str((ledger_dir / "transactions-2026.beancount").resolve())
        ids_to_delete = []
        for e in entries:
            if not isinstance(e, Transaction):
                continue
            if str(Path((e.meta or {}).get('filename', '')).resolve()) != txns_2026_path:
                continue
            ids_to_delete.append(e.meta['id'])

        assert len(ids_to_delete) > 0, "fixture precondition: 2026 file should have entries"
        mgr.delete_transactions_by_id(ids_to_delete)

        f = ledger_dir / "transactions-2026.beancount"
        assert f.is_file()
        text = f.read_text()
        assert "Employer" not in text
        assert "Grocery Store" not in text
        root_text = (ledger_dir / "main.beancount").read_text()
        assert 'include "transactions-2026.beancount"' in root_text


class TestNewEntryRouting:
    """New entries created in-app must land in the root file."""

    def test_create_transaction_lands_in_root_file(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _normalize_ledger(mgr)
        _clear_backups(backup_dir)
        snapshot = {p.name: p.read_bytes() for p in ledger_dir.glob("*.beancount") if p.name != "main.beancount"}

        from beancount.core.amount import Amount as _A
        txn = mgr.create_transaction_with_ids(
            date_obj=date(2026, 6, 1),
            payee="New Payee",
            narration="A genuinely new transaction",
            postings=[
                bd.Posting(account="Expenses:Food", units=_A(Decimal("50.00"), "USD"), cost=None, price=None, flag=None, meta=None),
                bd.Posting(account="Assets:Bank:Checking", units=_A(Decimal("-50.00"), "USD"), cost=None, price=None, flag=None, meta=None),
            ],
            source_account="Assets:Bank:Checking",
        )
        mgr.append_entries([txn])

        root_text = (ledger_dir / "main.beancount").read_text()
        assert "A genuinely new transaction" in root_text
        # Child files have no new entries and the on-disk content (post-
        # normalization) is byte-identical because nothing in them changed.
        for name, content in snapshot.items():
            assert (ledger_dir / name).read_bytes() == content, f"{name} should be untouched"
        # And the new transaction is stamped with the root filename.
        assert any(
            isinstance(e, Transaction) and "A genuinely new transaction" in (e.narration or "")
            and str(Path((e.meta or {}).get('filename', '')).resolve()) == str(root.resolve())
            for e in mgr._parse_ledger()[0]
        )

    def test_create_account_lands_in_root_file(self, tmp_path: Path):
        from app.schemas.account_schemas import AccountCreateRequest
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _normalize_ledger(mgr)
        _clear_backups(backup_dir)
        accounts_snapshot = (ledger_dir / "accounts.beancount").read_bytes()

        mgr.create_account_directive(AccountCreateRequest(
            name="Assets:Bank:NewAccount",
            open_date=date(2026, 1, 1),
            currencies=["USD"],
        ))

        root_text = (ledger_dir / "main.beancount").read_text()
        assert "Assets:Bank:NewAccount" in root_text
        assert (ledger_dir / "accounts.beancount").read_bytes() == accounts_snapshot

    def test_create_balance_directive_lands_in_root_file(self, tmp_path: Path):
        from app.schemas.account_schemas import BalanceDirectiveCreateRequest
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        backup_dir = tmp_path / "backups"
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, backup_dir)

        _normalize_ledger(mgr)
        _clear_backups(backup_dir)
        accounts_snapshot = (ledger_dir / "accounts.beancount").read_bytes()
        txn_snapshots = {
            p.name: p.read_bytes() for p in ledger_dir.glob("transactions-*.beancount")
        }

        mgr.add_balance_directive(
            "Assets:Bank:Checking",
            BalanceDirectiveCreateRequest(
                date=date(2026, 12, 31),
                currency="USD",
                amount=Decimal("9999.99"),
                include_pad=False,
                pad_source_account=None,
            ),
        )

        root_text = (ledger_dir / "main.beancount").read_text()
        assert "9999.99" in root_text
        assert (ledger_dir / "accounts.beancount").read_bytes() == accounts_snapshot
        for name, content in txn_snapshots.items():
            assert (ledger_dir / name).read_bytes() == content


class TestRootDirectivePreservation:
    """include / option / plugin lines at the root must survive a write."""

    def test_include_lines_survive_root_rewrite(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        # Force a root rewrite by appending an entry
        from app.schemas.account_schemas import AccountCreateRequest
        mgr.create_account_directive(AccountCreateRequest(
            name="Expenses:Newcat",
            open_date=date(2026, 1, 1),
            currencies=["USD"],
        ))

        root_text = (ledger_dir / "main.beancount").read_text()
        # All three original includes still present, as relative paths
        assert 'include "accounts.beancount"' in root_text
        assert 'include "transactions-2025.beancount"' in root_text
        assert 'include "transactions-2026.beancount"' in root_text

        # And the ledger re-parses cleanly
        _, errors, _ = mgr._parse_ledger()
        assert errors == []

    def test_user_set_option_directives_survive_root_rewrite(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        # Build a single-file ledger with user-set options + force a rewrite
        root = ledger_dir / "main.beancount"
        # ACCOUNTS_BLOCK sets operating_currency=USD; this test verifies a
        # custom title + render_commas survive across a write. We don't add
        # another operating_currency to avoid mixing lists.
        root.write_text(
            'option "title" "My Books"\n'
            'option "render_commas" "TRUE"\n'
            '\n'
            + ACCOUNTS_BLOCK + "\n" + TXNS_2025_BLOCK
        )
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        mgr.update_transactions_by_id([
            (target.meta['id'], target._replace(narration="updated")),
        ])

        _, errors, opts = mgr._parse_ledger()
        assert errors == []
        assert opts['title'] == "My Books"
        assert opts['operating_currency'] == ["USD"]
        assert opts['render_commas'] is True

    def test_default_option_lines_are_not_re_emitted(self, tmp_path: Path):
        """A ledger that never set name_assets must not gain a phantom
        `option "name_assets" "Assets"` line on first rewrite."""
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = ledger_dir / "main.beancount"
        root.write_text(ACCOUNTS_BLOCK + "\n" + TXNS_2025_BLOCK)  # only operating_currency
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="x"))])

        text = root.read_text()
        for default_key in ("name_assets", "name_equity", "name_expenses",
                            "name_income", "name_liabilities", "render_commas",
                            "account_previous_balances"):
            assert f'option "{default_key}"' not in text, f"phantom default option emitted: {default_key}"

    def test_plugin_directives_survive_root_rewrite(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = ledger_dir / "main.beancount"
        root.write_text(
            'plugin "beancount.plugins.implicit_prices"\n'
            '\n'
            + ACCOUNTS_BLOCK + "\n" + TXNS_2025_BLOCK
        )
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="y"))])

        text = root.read_text()
        assert 'plugin "beancount.plugins.implicit_prices"' in text


class TestPaddingFilterPerFile:
    """The padding-filter invariant (drop flag='P' entries without stable IDs)
    must hold per file in a multi-file write."""

    def test_padding_transaction_in_child_file_not_persisted(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        # Build a fixture where a child file has a pad+balance pair, which
        # makes Beancount generate a phantom 'P'-flag transaction at load
        # time. After we edit the child file, that phantom must not be
        # serialized to disk.
        (ledger_dir / "accounts.beancount").write_text(ACCOUNTS_BLOCK)
        (ledger_dir / "padded.beancount").write_text(
            """\
2025-01-15 * "Employer" "January salary"
  Assets:Bank:Checking        4500.00 USD
  Income:Salary              -4500.00 USD

2025-12-30 pad Assets:Bank:Checking Equity:Opening-Balances
2025-12-31 balance Assets:Bank:Checking  10000.00 USD
"""
        )
        root = ledger_dir / "main.beancount"
        root.write_text(
            'include "accounts.beancount"\n'
            'include "padded.beancount"\n'
        )
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="edited"))])

        # The serialized padded.beancount must NOT contain "Padding inserted"
        padded_text = (ledger_dir / "padded.beancount").read_text()
        assert "Padding inserted" not in padded_text
        # And no phantom 'P'-flag line
        assert "\nP " not in padded_text


class TestSingleFileRegression:
    """Single-file ledgers under the new code path must remain functional."""

    def test_single_file_write_yields_parseable_output(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_single_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        before_entries, _, _ = mgr._parse_ledger()
        target = _find_txn(before_entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="updated"))])

        after_entries, after_errors, _ = mgr._parse_ledger()
        assert after_errors == []
        # Same number of entries before and after
        assert len(after_entries) == len(before_entries)

    def test_single_file_write_preserves_operating_currency(self, tmp_path: Path):
        """The new path emits user-set options at the root — the small_ledger
        operating_currency line must survive across a write."""
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_single_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        _persist_ids_for_all_txns(mgr)
        entries, _, _ = mgr._parse_ledger()
        target = _find_txn(entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="x"))])

        _, _, opts = mgr._parse_ledger()
        assert opts['operating_currency'] == ["USD"]


class TestPluginEntryFiltering:
    """Plugin-synthesized entries (filename like '<auto_accounts>') must not
    be written to disk; the plugin regenerates them on next parse."""

    def test_plugin_generated_open_directives_dont_corrupt_writes(self, tmp_path: Path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        # Build a fixture that uses auto_accounts to synthesize Opens
        root = ledger_dir / "main.beancount"
        root.write_text(
            'plugin "beancount.plugins.auto_accounts"\n'
            'option "operating_currency" "USD"\n'
            '\n'
            '2025-01-15 * "Employer" "January salary"\n'
            '  Assets:Bank:Checking        4500.00 USD\n'
            '  Income:Salary              -4500.00 USD\n'
        )
        mgr = build_manager(root, tmp_path / "backups")

        # Parse — auto_accounts synthesizes Open directives
        before_entries, _, _ = mgr._parse_ledger()
        before_opens = [e for e in before_entries if isinstance(e, bd.Open)]
        assert len(before_opens) >= 2, "fixture precondition: auto_accounts should synthesize Opens"

        _persist_ids_for_all_txns(mgr)
        before_entries, _, _ = mgr._parse_ledger()
        before_opens = [e for e in before_entries if isinstance(e, bd.Open)]
        target = _find_txn(before_entries, "January salary")
        mgr.update_transactions_by_id([(target.meta['id'], target._replace(narration="x"))])

        # Re-parse: same plugin synthesizes the same Opens. Count must match.
        after_entries, after_errors, _ = mgr._parse_ledger()
        assert after_errors == []
        after_opens = [e for e in after_entries if isinstance(e, bd.Open)]
        assert len(after_opens) == len(before_opens)
        # Same account set
        assert {o.account for o in after_opens} == {o.account for o in before_opens}

        # And the on-disk root file MUST NOT contain literal Open directives
        # for accounts that auto_accounts would have synthesized (no
        # duplication on disk).
        root_text = root.read_text()
        assert "1970-01-01 open" not in root_text  # auto_accounts uses 2025-01-15
        # Count literal 'open' directive lines in the rewritten file; should
        # not exceed the plugin's own synthesis count.
        literal_open_lines = [
            l for l in root_text.splitlines()
            if " open " in l and not l.lstrip().startswith(";")
        ]
        assert len(literal_open_lines) == 0


class TestChildBeforeRootOrdering:
    """Crash-safety invariant: child files must be written before the root.
    This is the one test that asserts on ordering of internal calls because
    the property is load-bearing — the spec promises that if the loop is
    interrupted, the root still references the pre-write content of the
    children, not partially-updated content."""

    def test_child_files_written_before_root(self, tmp_path: Path, monkeypatch):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        root = build_flat_multi_file_ledger(ledger_dir)
        mgr = build_manager(root, tmp_path / "backups")

        order: list[str] = []
        original_atomic = mgr.backup_manager.atomic_write

        from contextlib import contextmanager as _cm

        @_cm
        def spy_atomic(path_str, *a, **kw):
            order.append(str(Path(path_str).resolve()))
            with original_atomic(path_str, *a, **kw) as fh:
                yield fh

        monkeypatch.setattr(mgr.backup_manager, "atomic_write", spy_atomic)

        # Trigger a write that touches multiple files. The first write of a
        # freshly-imported (un-normalized) multi-file fixture rewrites every
        # file once to normalize formatting — which is exactly the
        # multi-file scenario where ordering matters.
        from app.schemas.account_schemas import AccountCreateRequest
        mgr.create_account_directive(AccountCreateRequest(
            name="Expenses:NewOrderingCat",
            open_date=date(2026, 1, 1),
            currencies=["USD"],
        ))

        root_abs = str(root.resolve())
        assert root_abs in order, f"root file must be written, got {order}"
        # Multiple files were written
        assert len(order) > 1, f"expected at least one child + root, got {order}"
        # Root must be the last file written
        assert order[-1] == root_abs, f"expected root last, got order: {order}"

