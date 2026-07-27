"""
LedgerManager — thin orchestrator that delegates pure ledger logic to BeancountEngine.

Responsibilities:
- File I/O (atomic writes with backup)
- Transient ledger parsing via load_ledger_checked()
- Synchronous SQLite export after every write
- Ledger file switching

The engine handles all Beancount-specific logic: parsing, formatting, entry
creation, account/transaction/commodity/balance CRUD.
"""

import os
import functools
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import date
from decimal import Decimal
from contextlib import contextmanager
from beancount.core import data
from beancount.core.data import Transaction, Posting

from app.core.backup_manager import BackupManager
from app.core.beancount_engine import BeancountEngine
from app.core.ledger_loader import load_ledger_checked, discover_includes_per_file
from app.exceptions import APIError
from app import error_codes as ec
from app.write_lock import WriteLockManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData,
    BalanceDirectiveCreateRequest, BalanceDirectiveUpdateRequest
)
from app.schemas.commodity_schemas import (
    CommodityCreateRequest, CommodityCreateData
)

logger = logging.getLogger(__name__)


def _serialized_write(method):
    """Hold the per-user write lock across an entire read-modify-write.

    Every ledger mutation is: ``_parse_ledger()`` (read current state) →
    mutate the in-memory entry list → ``_write_and_export()`` (rewrite +
    re-export). Without this decorator the lock is taken only for the write
    itself, so two concurrent mutations can each parse the *same* pre-write
    snapshot and the second rewrite silently clobbers the first (a lost
    update — not corruption). Wrapping the whole method makes parse+write one
    critical section.

    The lock is reentrant per thread (``WriteLockManager`` uses an ``RLock``
    plus a depth-guarded file lock), so the inner ``_write_entries`` acquire
    is a safe no-op while this outer hold is active. Mutations that call other
    decorated mutations (reentrant nesting) are likewise safe.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._write_lock is not None:
            with self._write_lock.acquire(method.__name__):
                return method(self, *args, **kwargs)
        return method(self, *args, **kwargs)
    return wrapper


class LedgerManager:
    def __init__(
        self,
        ledger_file: str,
        backup_manager: BackupManager,
        ledger_initializer: LedgerInitializer,
        write_lock: Optional[WriteLockManager] = None,
        sqlite_exporter: Optional[Any] = None,
    ):
        self.ledger_file = ledger_file
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
        self.engine = BeancountEngine()
        self._write_lock = write_lock
        self._sqlite_exporter = sqlite_exporter

    def set_sqlite_exporter(self, exporter: Any) -> None:
        """Set the SQLite exporter for synchronous exports after writes."""
        self._sqlite_exporter = exporter

    def switch_ledger(self, new_ledger_file: str, *, create_if_missing: bool = False) -> Optional[str]:
        """Point the manager (and its initializer) at a new ledger file.

        Steps, in order: point the initializer at the new path, optionally
        materialise a new file from template, validate that the path is a
        readable regular file, then commit the switch by updating
        ``self.ledger_file``. If any step fails, the initializer's path is
        rolled back so it stays consistent with the still-active ledger.

        Returns a user-facing notice when a new file was created from the
        template, otherwise ``None``.

        This is the single writer to ``ledger_initializer.ledger_file``; the
        ``ConfigManager`` hot-switch flow goes through here instead of
        reaching into the initializer directly.
        """
        logger.info(f"Switching ledger file: {self.ledger_file} → {new_ledger_file}")
        previous_initializer_path = self.ledger_initializer.ledger_file
        self.ledger_initializer.ledger_file = new_ledger_file

        try:
            notice: Optional[str] = None
            if create_if_missing and not os.path.exists(new_ledger_file):
                created = self.ledger_initializer.ensure_ledger_exists()
                if not created:
                    raise APIError(
                        f"Ledger file does not exist and could not be created: {new_ledger_file}",
                        code=ec.LEDGER_CREATE_FAILED,
                        status_code=400,
                        details={"path": new_ledger_file},
                    )
                notice = f"Ledger file did not exist — a new ledger was created at {new_ledger_file}"
                logger.info(notice)

            if not os.path.isfile(new_ledger_file):
                raise APIError(
                    f"Ledger path is not a file: {new_ledger_file}",
                    code=ec.LEDGER_INVALID,
                    status_code=400,
                    details={"path": new_ledger_file},
                )

            if not os.access(new_ledger_file, os.R_OK):
                raise APIError(
                    f"Ledger file is not readable: {new_ledger_file}",
                    code=ec.LEDGER_NOT_READABLE,
                    status_code=400,
                    details={"path": new_ledger_file},
                )

            self.ledger_file = new_ledger_file
            return notice
        except Exception:
            # Roll back the initializer so it stays consistent with the
            # still-active ledger_file we never moved to.
            self.ledger_initializer.ledger_file = previous_initializer_path
            raise

    # ── Transient parse ─────────────────────────────────────────────────────

    def _parse_ledger(self) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
        """Parse ledger from disk transiently. Memory released when caller returns."""
        return load_ledger_checked(self.ledger_file)

    # ── Delegated read helpers (for internal validation in write methods) ────

    def is_existing_account(self, account_name: str) -> bool:
        entries, _, _ = self._parse_ledger()
        for e in entries:
            if isinstance(e, data.Open) and e.account == account_name:
                return True
        return False

    def validate_account_format(self, account_name: str) -> bool:
        return self.engine.validate_account_format(account_name)

    def has_parsing_errors(self) -> bool:
        if not os.path.exists(self.ledger_file):
            return False
        try:
            _, errors, _ = self._parse_ledger()
            return len(errors) > 0
        except Exception:
            return True

    # ── Write helpers ────────────────────────────────────────────────────────

    @contextmanager
    def atomic_ledger_write(self, file_path: Optional[str] = None):
        """Context manager for atomic ledger writes."""
        target_file = file_path or self.ledger_file
        with self.backup_manager.atomic_write(target_file) as f:
            yield f

    def _write_entries(self, entries, options: Optional[Dict[str, Any]] = None) -> None:
        """Write all entries to the ledger using the engine's formatter.

        This is the single authorised path for mutating the ledger file.
        When a WriteLockManager is present (multi-user / concurrent access),
        the write is serialised under the per-user lock.

        ``options`` is the third element of ``_parse_ledger()``. It carries the
        per-load ``include``/``plugin``/``option`` directives that must be
        re-emitted into the root file on rewrite so that a multi-file ledger
        is not collapsed on the first write. When ``None`` (single-file
        callers), no extra preamble is emitted.
        """
        if self._write_lock:
            with self._write_lock.acquire("_write_entries"):
                self._do_write_entries(entries, options)
        else:
            self._do_write_entries(entries, options)

    def _do_write_entries(self, entries, options: Optional[Dict[str, Any]] = None) -> None:
        """Perform the actual atomic write per source file (called by ``_write_entries``).

        Multi-file ledgers: entries are grouped by ``meta['filename']`` (which
        the parser stamps with the absolute path of the file each entry came
        from). Each group is written through ``atomic_ledger_write`` against
        its own file. New entries created in-app are stamped with the root
        ``ledger_file`` and so land in the root.

        Order: child files first, root last. If a child write fails after the
        root has already been updated, the root could reference content the
        children haven't received — writing the root last keeps the include
        map referencing the still-valid pre-write child files until the very
        last step.

        Single-file ledgers collapse to a single group and the loop runs once,
        producing the same output as before — except that user-set ``option``
        and ``plugin`` directives at the root are now preserved across writes
        (previously stripped because they aren't entries).
        """
        root_abs = str(Path(self.ledger_file).resolve())

        # Discover the per-file include map up front so we can preserve nested
        # `include` directives (root → A → B). Best-effort: if a child is
        # missing or unparseable, ``discover_includes_per_file`` returns []
        # for that file and we just emit no include lines.
        try:
            include_map = discover_includes_per_file(root_abs)
        except Exception:
            logger.exception("discover_includes_per_file failed; falling back to root-only includes")
            include_map = {root_abs: list((options or {}).get('include') or [])}

        known_files = set(include_map.keys())

        # Fava-compatible new-entry routing. If the ledger carries
        # ``custom "fava-option" "insert-entry"`` directives, re-stamp the
        # ``meta['filename']`` of genuinely new entries (the ones currently
        # bound for the root) so they land in the file Fava would choose. This
        # is the *only* routing step — the grouping + per-file full-rewrite
        # below then places each entry by ``meta['filename']`` exactly as it
        # already does for parsed entries. No second write path. See
        # dev-docs/multi-file-ledger.md.
        entries = self._route_new_entries(entries, root_abs, known_files)

        # Group entries by their source filename. Entries whose filename is
        # missing or doesn't resolve to a known file in the include tree are
        # treated as either (a) new entries, routed to root; or (b)
        # plugin-synthesized, filtered out — distinguished by whether the
        # filename looks like a real path.
        groups: Dict[str, list] = defaultdict(list)
        for e in entries:
            raw_fname = (e.meta or {}).get('filename')
            if not raw_fname:
                groups[root_abs].append(e)
                continue
            try:
                abs_fname = str(Path(raw_fname).resolve())
            except (OSError, ValueError):
                # Unresolvable path — almost certainly synthetic (e.g. '<plugin>')
                continue
            if abs_fname in known_files:
                groups[abs_fname].append(e)
            elif raw_fname.startswith('<') and raw_fname.endswith('>'):
                # Plugin-synthesized entry; the plugin will regenerate it on
                # the next parse. Skip silently.
                continue
            elif Path(raw_fname).is_file():
                # A real file but not in the include tree — defensive: route
                # to root rather than dropping data.
                groups[root_abs].append(e)
            else:
                # Synthetic-looking and unresolvable — drop.
                continue

        # Ensure every known file in the include tree is represented, even
        # if no entries point at it any more (e.g. the user just deleted the
        # last entry from a per-year child file — that file should be
        # rewritten to empty, not left stale). The content-equality check
        # below means files with unchanged content are still skipped.
        for known in known_files:
            if known not in groups:
                groups[known] = []

        # Sort: children first, root last.
        ordered_files = [f for f in groups if f != root_abs] + [root_abs]

        for fname in ordered_files:
            # Sort the group by Beancount's own sort key (date, type-priority,
            # lineno). This matches the order the loader returns on re-parse,
            # so the byte-equality skip below works on every subsequent write
            # — without this, entries appended out of date order (e.g. by
            # ``append_entries``) land on disk in append-order, the next
            # re-parse sorts them, and the next write rewrites the file with
            # no content change other than the reordering.
            group = sorted(
                groups[fname],
                key=lambda e: (
                    e.date,
                    data.SORT_ORDER.get(type(e), 0),
                    (e.meta or {}).get('lineno', 0),
                ),
            )
            # Re-express any absolutized Document directive filenames relative
            # to the file they're written into. Beancount absolutizes these on
            # parse; without this the printer would bake absolute, non-portable
            # paths into the ledger on every rewrite. See engine docstring.
            file_dir = Path(fname).parent
            group = [self.engine.relativize_document_path(e, file_dir) for e in group]

            preamble_parts: List[str] = []

            if fname == root_abs and options:
                opt_text = self.engine.format_options(options)
                if opt_text:
                    preamble_parts.append(opt_text)
                plugin_text = self.engine.format_plugins(options)
                if plugin_text:
                    preamble_parts.append(plugin_text)

            file_includes = include_map.get(fname) or []
            if file_includes:
                inc_text = self.engine.format_includes(file_includes, relative_to=Path(fname).parent)
                if inc_text:
                    preamble_parts.append(inc_text)

            preamble = ''.join(preamble_parts)
            new_content = (preamble + '\n' if preamble else '') + self.engine.format_entries(group)

            # Skip files whose serialized content is byte-identical to what's
            # already on disk — no rewrite, no backup. This keeps single-file
            # edits from touching unrelated child files in a multi-file ledger.
            fpath = Path(fname)
            if fpath.is_file():
                try:
                    existing = fpath.read_bytes()
                except OSError:
                    existing = None
                if existing is not None and existing == new_content.encode('utf-8'):
                    continue

            with self.atomic_ledger_write(file_path=fname) as f:
                f.seek(0)
                f.truncate()
                f.write(new_content)

    def _route_new_entries(self, entries, root_abs: str, known_files: set) -> list:
        """Re-stamp ``meta['filename']`` on new entries per Fava insert-entry rules.

        A *new* entry is one the app just created: it carries
        ``meta['lineno'] == 0`` and a ``meta['filename']`` that resolves to the
        root file (every engine ``create_*`` stamps the root). Parsed entries
        always have a real (1-based) lineno, and edited entries carry their
        original source location via ``_carry_source_location`` — so both are
        left untouched and stay in their own file, matching Fava (only new
        entries route).

        Routing only targets files already in the include tree
        (``known_files``); a rule or ``default-file`` pointing outside the tree
        is ignored and the entry stays at the root, because we never create new
        files or ``include`` lines (also matching Fava). Plugin-synthesized
        entries (filename like ``<...>``) never resolve to the root and so are
        never touched here — important, since re-stamping one would defeat the
        synthetic-entry filter in the grouping step below.
        """
        rules, default_file = self.engine.parse_routing_directives(entries)
        if not rules and not default_file:
            return entries

        routed = []
        for e in entries:
            meta = e.meta or {}
            if meta.get('lineno', 0) != 0:
                routed.append(e)
                continue
            raw_fname = meta.get('filename')
            if not raw_fname:
                routed.append(e)
                continue
            try:
                resolved = str(Path(raw_fname).resolve())
            except (OSError, ValueError):
                routed.append(e)
                continue
            if resolved != root_abs:
                routed.append(e)
                continue

            target = self.engine.route_entry(e, rules, default_file, root_abs)
            if target != root_abs and target in known_files:
                routed.append(e._replace(meta={**meta, 'filename': target}))
            else:
                routed.append(e)
        return routed

    def _write_and_export(self, entries, errors=None, options=None) -> None:
        """Write entries then synchronously export to SQLite.

        This replaces the old pattern of write → cache invalidation → debounced export.
        The export happens inline so that reads immediately reflect the write.

        Always re-parses after writing to get fresh errors/options, because the
        write may have resolved or introduced validation errors (e.g. changing an
        account open date can fix "inactive account" errors).

        Two-parse policy: this method intentionally re-parses (parse 2) after
        the write to refresh errors. Combined with parse 1 in the API-handler
        (which read current state to build ``entries``), every mutation pays
        two full ledger parses. This is the deliberate cost of the design
        constraints: (a) no in-memory ledger cache (parsing is the architectural
        choice — see ``ledger_loader.py``), and (b) post-write errors must be
        immediately visible. Bulk imports batch into a single ``append_entries``
        call, so a 1000-transaction import is still 2 parses, not 2000.
        """
        self._write_entries(entries, options)

        if self._sqlite_exporter:
            entries, errors, options = self._parse_ledger()
            try:
                self._sqlite_exporter.export_full_sync(
                    entries, errors, options, ledger_file=str(self.ledger_file)
                )
            except Exception as e:
                logger.error(
                    "SQLite export failed after write (data is in .beancount, "
                    "will be recovered on next read): %s", e
                )

    @_serialized_write
    def append_entries(self, new_entries) -> None:
        """Append new entries via a full rewrite through _write_and_export()."""
        entries, errors, options = self._parse_ledger()
        all_entries = list(entries) + list(new_entries)
        self._write_and_export(all_entries, errors, options)

    # ── Account management (orchestrator: parse → engine → write) ────────────

    @_serialized_write
    def create_account_directive(self, request: AccountCreateRequest) -> AccountCreateData:
        if not self.validate_account_format(request.name):
            raise ValueError(f"Invalid account format: {request.name}")

        entries, errors, options = self._parse_ledger()

        # Check if account already exists
        for e in entries:
            if isinstance(e, data.Open) and e.account == request.name:
                raise ValueError(f"Account already exists: {request.name}")

        metadata = {}
        if request.description:
            metadata['description'] = request.description
        if request.metadata:
            metadata.update(request.metadata)

        new_entries = self.engine.create_account(
            list(entries),
            name=request.name,
            open_date=request.open_date,
            currencies=request.currencies or [],
            metadata=metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

        # Return success — account details come from SqliteReader
        return AccountCreateData(
            account_created=True,
            account_details=None,
            message=f"Account '{request.name}' created successfully",
        )

    @_serialized_write
    def update_account_directive(
        self,
        account_name: str,
        *,
        new_name: Optional[str] = None,
        open_date: Optional[date] = None,
        currencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        close_date: Optional[date] = None,
        reopen: bool = False,
    ) -> None:
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.update_account(
            list(entries),
            account_name,
            new_name=new_name,
            open_date=open_date,
            currencies=currencies,
            metadata=metadata,
            close_date=close_date,
            reopen=reopen,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def close_account_directive(self, account_name: str, close_date, reason: Optional[str] = None) -> None:
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.close_account(
            list(entries),
            account_name, close_date,
            reason=reason,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def reopen_account_directive(self, account_name: str) -> None:
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.reopen_account(list(entries), account_name)
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def delete_account_directive(self, account_name: str) -> None:
        """Remove Open/Close directives only (no transaction deletion)."""
        entries, errors, options = self._parse_ledger()
        new_entries = [
            e for e in entries
            if not (
                (isinstance(e, data.Open) and e.account == account_name) or
                (isinstance(e, data.Close) and e.account == account_name)
            )
        ]
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def delete_account(self, account_name: str, delete_transactions: bool = True) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, txn_deleted = self.engine.delete_account(
            list(entries), account_name, delete_transactions
        )
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted account {account_name} and {txn_deleted} transaction(s)")
        return txn_deleted

    # ── Budget management (custom "budget" directives) ──────────────────────

    @_serialized_write
    def create_budget_directive(
        self, *, date_obj, account: str, interval: str, amount, currency: str,
    ) -> None:
        """Append a new `custom "budget"` directive to the root ledger file."""
        from app.core.budget_directives import build_budget_custom
        entries, errors, options = self._parse_ledger()
        meta = {"filename": str(self.ledger_file), "lineno": 0}
        entry = build_budget_custom(meta, date_obj, account, interval, amount, currency)
        self._write_and_export(list(entries) + [entry], errors, options)

    def _find_budget_index(self, entries, budget_id_str: str):
        from app.core.budget_directives import parse_budget_entry
        for i, e in enumerate(entries):
            fields = parse_budget_entry(e)
            if fields and fields["id"] == budget_id_str:
                return i
        return None

    @_serialized_write
    def update_budget_directive(
        self, budget_id_str: str, *, date_obj, account: str, interval: str, amount, currency: str,
    ) -> bool:
        """Replace the budget directive identified by ``budget_id_str``, keeping
        it in its original source file (via _carry_source_location). Returns
        False if no such directive exists."""
        from app.core.budget_directives import build_budget_custom
        entries, errors, options = self._parse_ledger()
        idx = self._find_budget_index(entries, budget_id_str)
        if idx is None:
            return False
        original = entries[idx]
        new_meta = self.engine._carry_source_location(original.meta, {})
        new_entry = build_budget_custom(new_meta, date_obj, account, interval, amount, currency)
        new_entries = list(entries)
        new_entries[idx] = new_entry
        self._write_and_export(new_entries, errors, options)
        return True

    @_serialized_write
    def delete_budget_directive(self, budget_id_str: str) -> bool:
        """Remove the budget directive identified by ``budget_id_str``. Returns
        False if no such directive exists."""
        entries, errors, options = self._parse_ledger()
        idx = self._find_budget_index(entries, budget_id_str)
        if idx is None:
            return False
        new_entries = [e for i, e in enumerate(entries) if i != idx]
        self._write_and_export(new_entries, errors, options)
        return True

    # ── Commodity management ────────────────────────────────────────────────

    @_serialized_write
    def create_commodity_directive(self, request: CommodityCreateRequest) -> CommodityCreateData:
        entries, errors, options = self._parse_ledger()

        # Check if commodity already exists
        for e in entries:
            if isinstance(e, data.Commodity) and e.currency == request.code:
                raise ValueError(f"Commodity already exists: {request.code}")

        new_entries = self.engine.create_commodity(
            list(entries),
            code=request.code,
            name=request.name,
            commodity_type=request.type,
            metadata=request.metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

        return CommodityCreateData(
            commodity_created=True,
            commodity_details=None,
            message=f"Commodity '{request.code}' created successfully",
        )

    # ── Document directive management (account-level documents) ──────────────

    @_serialized_write
    def create_document_attachment(
        self,
        *,
        date_obj: date,
        account_name: str,
        filename: str,
        tags: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Attach a document to an account via a ``Document`` directive."""
        entries, errors, options = self._parse_ledger()

        acct_exists = any(isinstance(e, data.Open) and e.account == account_name for e in entries)
        if not acct_exists:
            raise ValueError(f"Account not found: {account_name}")

        new_entries = self.engine.create_document(
            list(entries),
            date_obj=date_obj,
            account_name=account_name,
            filename=filename,
            tags=tags,
            links=links,
            metadata=metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def delete_document_directive(self, *, account_name: str, filename: str) -> int:
        """Remove the ``Document`` directive(s) matching account + filename.

        ``filename`` is compared after resolving both sides to absolute paths,
        since Beancount absolutizes Document filenames on load while the stored
        value is ledger-relative.
        """
        entries, errors, options = self._parse_ledger()
        ledger_dir = Path(self.ledger_file).resolve().parent
        target = (ledger_dir / filename).resolve()

        remaining = []
        removed = 0
        for e in entries:
            if isinstance(e, data.Document) and e.account == account_name:
                try:
                    e_abs = Path(e.filename).resolve()
                except OSError:
                    e_abs = None
                if e_abs == target:
                    removed += 1
                    continue
            remaining.append(e)

        if removed:
            self._write_and_export(remaining, errors, options)
        return removed

    @_serialized_write
    def ensure_documents_option(self, documents_root: "Path") -> bool:
        """Write ``option "documents" "<rel>"`` into the ledger if it has none.

        Called on first document upload so Fava auto-discovery resolves to the
        same root Finzytrack uses (decision: auto-write on first use). Idempotent
        — a no-op when the ledger already declares the option. Returns whether a
        write occurred.
        """
        from app.core.document_store import documents_option_value

        entries, errors, options = self._parse_ledger()
        if (options or {}).get("documents"):
            return False

        value = documents_option_value(
            documents_root=documents_root, ledger_file=str(self.ledger_file)
        )
        new_options = dict(options or {})
        new_options["documents"] = [value]
        self._write_and_export(list(entries), errors, new_options)
        logger.info("Auto-wrote option \"documents\" \"%s\" to ledger", value)
        return True

    def resolve_documents_root(self, default_dir: str) -> "Path":
        """Resolve the documents storage root from the ledger's
        ``option "documents"`` (if any), else ``default_dir``."""
        from app.core.document_store import resolve_documents_root
        _, _, options = self._parse_ledger()
        return resolve_documents_root(
            ledger_file=str(self.ledger_file), options=options, default_dir=default_dir
        )

    def scan_orphan_documents(self, *, documents_root: "Path"):
        """Scan the documents root for files referenced by nothing in the
        *entire* loaded ledger (all ``include``d files) — both ``document*``
        metadata and ``Document`` directives (invariant I7). Returns every
        unreferenced file; the recency split is a UI concern."""
        from app.core.document_store import DocumentStore, collect_referenced_paths
        entries, _, _ = self._parse_ledger()
        store = DocumentStore(documents_root, Path(self.ledger_file).resolve().parent)
        referenced = collect_referenced_paths(entries)
        return store.scan_orphans(referenced=referenced)

    def delete_orphan_documents(self, *, documents_root: "Path", paths):
        """Delete selected orphan files, re-validating each is *still* an orphan
        against the current ledger immediately before unlinking (invariant I8).

        Serialised under the per-user write lock so the re-validation can't
        interleave with a concurrent ledger write that attaches one of the
        files mid-sweep.
        """
        if self._write_lock:
            with self._write_lock.acquire("orphan_document_delete"):
                return self._delete_orphans_locked(documents_root, paths)
        return self._delete_orphans_locked(documents_root, paths)

    def _delete_orphans_locked(self, documents_root: "Path", paths):
        from app.core.document_store import DocumentStore, collect_referenced_paths
        entries, _, _ = self._parse_ledger()
        store = DocumentStore(documents_root, Path(self.ledger_file).resolve().parent)
        referenced = collect_referenced_paths(entries)
        return store.delete(paths=list(paths), referenced=referenced)

    # ── Transaction management ──────────────────────────────────────────────

    def create_transaction_with_ids(
        self,
        date_obj: date,
        payee: str,
        narration: str,
        postings: List[Posting],
        source_account: str,
        flag: str = '*',
        external_id: Optional[str] = None,
        external_id_type: Optional[str] = None,
        additional_meta: Optional[Dict] = None,
    ) -> Transaction:
        return self.engine.create_transaction(
            date_obj, payee, narration, postings, source_account,
            flag=flag, external_id=external_id,
            external_id_type=external_id_type,
            additional_meta=additional_meta,
            ledger_file=str(self.ledger_file),
        )

    def add_ids_to_transaction(self, txn: Transaction, force_regenerate: bool = False) -> Transaction:
        return self.engine.add_ids_to_transaction(txn, force_regenerate)

    def compute_hash_from_transaction(self, txn: Transaction) -> Tuple[str, str]:
        return self.engine.compute_hash_from_transaction(txn)

    @_serialized_write
    def update_transactions_by_id(self, transactions: List[Tuple[str, Transaction]]) -> int:
        entries, errors, options = self._parse_ledger()
        updated_entries, count = self.engine.update_transactions(list(entries), transactions)
        self._write_and_export(updated_entries, errors, options)
        logger.info(f"Updated {count} transactions in ledger")
        return count

    @_serialized_write
    def delete_transactions_by_id(self, transaction_ids: List[str]) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, count = self.engine.delete_transactions(list(entries), transaction_ids)
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted {count} transaction(s) from ledger")
        return count

    @_serialized_write
    def delete_transactions_for_account(self, account_name: str) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, count = self.engine.delete_transactions_for_account(list(entries), account_name)
        if count == 0:
            return 0
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted {count} transaction(s) for account {account_name}")
        return count

    def validate_transaction(self, transaction: Transaction) -> List[str]:
        return self.engine.validate_transaction(transaction)

    # ── Balance & pad directive management ──────────────────────────────────

    @_serialized_write
    def add_balance_directive(self, account_name: str, request: BalanceDirectiveCreateRequest) -> None:
        entries, errors, options = self._parse_ledger()

        # Validate account exists
        acct_exists = any(isinstance(e, data.Open) and e.account == account_name for e in entries)
        if not acct_exists:
            raise ValueError(f"Account not found: {account_name}")
        if request.include_pad:
            if not request.pad_source_account:
                raise ValueError("pad_source_account is required when include_pad is True")
            pad_exists = any(isinstance(e, data.Open) and e.account == request.pad_source_account for e in entries)
            if not pad_exists:
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.add_balance_directive(
            list(entries),
            account_name, request.date, request.currency, request.amount,
            include_pad=request.include_pad,
            pad_source_account=request.pad_source_account,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def update_balance_directive(self, account_name: str, request: BalanceDirectiveUpdateRequest) -> None:
        entries, errors, options = self._parse_ledger()

        acct_exists = any(isinstance(e, data.Open) and e.account == account_name for e in entries)
        if not acct_exists:
            raise ValueError(f"Account not found: {account_name}")
        if request.pad_source_account:
            pad_exists = any(isinstance(e, data.Open) and e.account == request.pad_source_account for e in entries)
            if not pad_exists:
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.update_balance_directive(
            list(entries),
            account_name,
            original_date=request.original_date,
            original_currency=request.original_currency,
            original_amount=request.original_amount,
            new_date=request.new_date,
            new_currency=request.new_currency,
            new_amount=request.new_amount,
            include_pad=request.include_pad,
            pad_source_account=request.pad_source_account,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    @_serialized_write
    def delete_balance_directive(
        self, account_name: str,
        directive_date: str, currency: str, amount: Decimal,
        delete_pad: bool = True,
    ) -> None:
        from datetime import datetime as dt
        target_date = dt.strptime(directive_date, "%Y-%m-%d").date()

        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.delete_balance_directive(
            list(entries),
            account_name, target_date, currency, amount,
            delete_pad=delete_pad,
        )
        self._write_and_export(new_entries, errors, options)

    # ── Compat aliases ───────────────────────────────────────────────────────

    @staticmethod
    def _find_pad_before_balance_entry(entries: list, balance_idx: int, account_name: str):
        return BeancountEngine._find_pad_before_balance_entry(entries, balance_idx, account_name)

    @staticmethod
    def _day_before(date_str: str) -> str:
        from datetime import timedelta, datetime
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - timedelta(days=1)).isoformat()

    @staticmethod
    def _format_amount(amount: Decimal) -> str:
        return BeancountEngine._format_amount(amount)

    @staticmethod
    def _amounts_match(file_amount: Decimal, expected_amount: Decimal) -> bool:
        return BeancountEngine._amounts_match(file_amount, expected_amount)

    def _rename_account_in_entry(self, entry, old_name: str, new_name: str):
        return self.engine.rename_account_in_entry(entry, old_name, new_name)
