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
from app.core.ledger_loader import load_ledger_checked, load_string_checked, discover_includes_per_file, sidecar_path, MemoryPressureError
from app.exceptions import APIError
from app import error_codes as ec
from app.write_lock import WriteLockManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData,
    BalanceDirectiveCreateRequest, BalanceDirectiveUpdateRequest
)
from app.schemas.commodity_schemas import CommodityMetadataChange
from app.core.metadata_vocabulary import SCOPE_COMMODITY, to_ledger_value

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

    @staticmethod
    def _is_booking_error(err) -> bool:
        """A **booking-phase** error (``beancount.parser.booking*``) — "Ambiguous
        matches", "Not enough lots to reduce", etc. These are the errors a bare
        ``parser.parse_string`` cannot see (booking runs only on a full load), so they
        are exactly what the write-path gate exists to catch before a durable write.

        Deliberately narrow: balance-*assertion* mismatches
        (``interpolate.BalanceError``) and document-reference errors
        (``documents.DocumentError``) are **informational and legitimately
        persistable** — a failing ``balance`` assertion is the user's own "does this
        still match the broker?" signal, not corruption — so they must never block a
        write. Other validation errors (unknown account, transaction imbalance) are
        already surfaced on today's paths (the commit endpoint's parse check, client-side
        balance validation) and stay write-then-surface; the gate only *adds* the booking
        class, regressing nothing. (This narrows dev-docs/investment-view.md §6.6's
        "any new error" to booking errors — see the note there.)
        """
        return type(err).__module__.startswith("beancount.parser.booking")

    def _candidate_new_errors(self, entries, baseline_errors, options) -> List[str]:
        """Validate a candidate entry list **in memory** and return the **booking**
        errors this change would introduce (the write-path gate; §6.6).

        Format the whole candidate as one self-contained string (options + plugins
        preamble, no ``include`` lines — the entry stream already carries every file's
        entries) and run it back through the loader so booking runs. Nothing touches disk.

        The "new error" signal is deliberately **cheap and location-insensitive**, not a
        per-error diff (a rewrite shifts source locations, so ``(file, lineno)`` matching
        is unreliable): compare the **count of booking errors** before vs after — any
        increase is treated as introduced (biased to over-report). Returns ``[]`` when no
        new booking error (so the write proceeds).
        """
        try:
            preamble_parts: List[str] = []
            if options:
                opt_text = self.engine.format_options(options)
                if opt_text:
                    preamble_parts.append(opt_text)
                plugin_text = self.engine.format_plugins(options)
                if plugin_text:
                    preamble_parts.append(plugin_text)
            preamble = ''.join(preamble_parts)
            candidate_text = (preamble + '\n' if preamble else '') + self.engine.format_entries(list(entries))
            _cand_entries, cand_errors, _cand_options = load_string_checked(candidate_text)
        except MemoryPressureError:
            raise
        except Exception:
            # A validator bug must never block an otherwise-valid write. Fall back to
            # the historical write-then-surface behavior (the post-write reparse still
            # records any real errors into the mirror).
            logger.exception("Pre-write validation could not run; proceeding without the gate")
            return []

        baseline_booking = sum(1 for e in (baseline_errors or []) if self._is_booking_error(e))
        candidate_booking = [e for e in cand_errors if self._is_booking_error(e)]
        if len(candidate_booking) <= baseline_booking:
            return []
        return [str(getattr(e, 'message', e)) for e in candidate_booking]

    def _write_and_export(self, entries, errors=None, options=None, *, acknowledge: bool = False) -> None:
        """Write entries then synchronously export to SQLite.

        This replaces the old pattern of write → cache invalidation → debounced export.
        The export happens inline so that reads immediately reflect the write.

        **Check-then-write gate (dev-docs/investment-view.md §6.6).** Before the durable
        write, the candidate entry list is validated in memory (``_candidate_new_errors``);
        if the change would introduce a new load error and the caller hasn't
        ``acknowledge``-d it, we **raise and do not write** — nothing broken is silently
        persisted. The atomic swap itself is unchanged (this is "check, then write," never
        "write, then undo"), and the whole sequence runs inside the caller's
        ``@_serialized_write`` critical section, so no interleaving write can change the
        inventory between validation and swap. Passing ``errors=None`` skips the gate.

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
        call, so a 1000-transaction import is still 2 parses, not 2000. The gate
        adds one in-memory ``load_string`` (no disk) on the pre-write path.
        """
        if errors is not None and not acknowledge:
            new_errors = self._candidate_new_errors(entries, errors, options)
            if new_errors:
                raise APIError(
                    message=(
                        "This change can't be saved — it would put the ledger in an "
                        "invalid state: " + "; ".join(new_errors[:5])
                    ),
                    code=ec.LEDGER_VALIDATION_FAILED,
                    status_code=422,
                    details={"errors": new_errors, "introduced_by_change": True},
                )

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
    def append_entries(self, new_entries, *, acknowledge: bool = False) -> None:
        """Append new entries via a full rewrite through _write_and_export().

        ``acknowledge=True`` bypasses the pre-write validation gate (§6.6) — the
        interactive "Register anyway" path, after the user has seen the introduced
        errors and chosen to proceed.
        """
        entries, errors, options = self._parse_ledger()
        all_entries = list(entries) + list(new_entries)
        self._write_and_export(all_entries, errors, options, acknowledge=acknowledge)

    # ── Price sidecar (dedicated, transaction-free write path) ────────────────

    @staticmethod
    def _render_price_sidecar(prices) -> str:
        """Render ``data.Price`` entries as a ``prices.beancount`` file, sorted by
        (date, base, quote). Every write rewrites the whole file from its
        entries, so comments and price metadata are not kept."""
        lines = [
            "; -*- mode: beancount; coding: utf-8; -*-",
            "; Price sidecar — written by Finzytrack (price fetch and the Prices tab).",
            "; Not `include`d in the ledger; loaded only for valuation.",
            "; Edits here are kept; comments are not (every save rewrites this file).",
            "",
        ]
        for p in sorted(prices, key=lambda e: (e.date, e.currency, e.amount.currency)):
            # format 'f': never an exponent (1E-7), which Beancount can't read.
            lines.append(
                f"{p.date.isoformat()} price {p.currency} {format(p.amount.number, 'f')} {p.amount.currency}"
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _price_key(p) -> Tuple[Any, str, str]:
        return (p.date, p.currency, p.amount.currency)

    def _apply_price_changes(
        self, set_prices: List[Any], remove_keys: List[Tuple[Any, str, str]], *, strict: bool,
        keep_existing: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Shared body of the two sidecar writers: merge by (date, base, quote),
        check the rendered file parses back to the same prices, swap it in
        atomically, re-export the mirror. Caller holds the write lock.

        Keys that also exist as a ``price`` in the ledger file are left to the
        ledger: ``strict`` (a user edit) rejects them, otherwise (a fetch) they
        are skipped — two prices for one key make "latest" ambiguous. ``strict``
        also rejects removing a key the sidecar doesn't have, and setting a key in
        ``keep_existing`` that the sidecar already has (409 ``PRICE_EXISTS`` — the
        caller asked not to replace it). A sidecar with lines that don't parse is
        never rewritten (that would drop them): 422 ``PRICE_SIDECAR_INVALID``.
        """
        path = sidecar_path(self.ledger_file)
        merged: Dict[Tuple[Any, str, str], Any] = {}
        if path.exists():
            existing, sidecar_errors, _opts = load_ledger_checked(str(path))
            if sidecar_errors:
                raise APIError(
                    message="The price file has lines that can't be read. Fix them in the file, then try again.",
                    code=ec.PRICE_SIDECAR_INVALID,
                    status_code=422,
                    details={"file": str(path), "errors": [
                        f"line {getattr(e, 'source', {}).get('lineno', '?')}: {getattr(e, 'message', e)}"
                        for e in sidecar_errors
                    ][:5]},
                )
            for e in existing:
                if isinstance(e, data.Price):
                    merged[self._price_key(e)] = e

        entries, errors, options = self._parse_ledger()
        in_ledger = {self._price_key(e) for e in entries if isinstance(e, data.Price)}

        def _fmt(k) -> Dict[str, str]:
            return {"date": k[0].isoformat(), "commodity": k[1], "quote_currency": k[2]}

        if strict:
            touched = {self._price_key(p) for p in set_prices} | set(remove_keys)
            clashes = sorted(k for k in touched if k in in_ledger)
            if clashes:
                raise APIError(
                    message="This date has a price in your ledger file. Change it there.",
                    code=ec.PRICE_IN_LEDGER,
                    status_code=422,
                    details={"prices": [_fmt(k) for k in clashes]},
                )
            missing = sorted(k for k in set(remove_keys) if k not in merged)
            if missing:
                raise APIError(
                    message="Price not found in the price file",
                    code=ec.PRICE_NOT_FOUND,
                    status_code=404,
                    details={"prices": [_fmt(k) for k in missing]},
                )
            removing = set(remove_keys)
            taken = sorted(
                k for k in (keep_existing or set()) if k in merged and k not in removing
            )
            if taken:
                raise APIError(
                    message="This date already has a price.",
                    code=ec.PRICE_EXISTS,
                    status_code=409,
                    details={"prices": [
                        {**_fmt(k), "number": format(merged[k].amount.number, "f")} for k in taken
                    ]},
                )

        removed = 0
        for k in remove_keys:
            if k in merged and k not in in_ledger:
                del merged[k]
                removed += 1

        added = updated = 0
        for p in set_prices:
            key = self._price_key(p)
            if key in in_ledger:
                continue
            if key not in merged:
                added += 1
            elif merged[key].amount.number != p.amount.number:
                updated += 1
            merged[key] = p  # new wins → idempotent replace

        text = self._render_price_sidecar(merged.values())
        # Check-then-write (cf. §6.6): the file must parse back cleanly to
        # exactly the prices we meant to write before it replaces the old one.
        parsed, parse_errors, _ = load_string_checked(text)
        back = {self._price_key(e): e.amount.number for e in parsed if isinstance(e, data.Price)}
        if parse_errors or back != {k: v.amount.number for k, v in merged.items()}:
            raise APIError(
                message="The price file would not read back correctly; nothing was saved.",
                code=ec.PRICE_SIDECAR_INVALID,
                status_code=422,
                details={"errors": [str(getattr(e, "message", e)) for e in parse_errors][:5]},
            )

        with self.backup_manager.atomic_write(str(path)) as f:
            f.write(text)

        # A sidecar that didn't exist at the last export isn't recorded in the
        # freshness fingerprint (an absent file is deliberately not fingerprinted
        # — Slice 0), so a stat-based staleness check wouldn't notice the first
        # write. Re-export explicitly so the prices are queryable immediately.
        if self._sqlite_exporter:
            try:
                self._sqlite_exporter.export_full_sync(
                    entries, errors, options, ledger_file=str(self.ledger_file)
                )
            except Exception as e:
                logger.error("SQLite re-export after price write failed: %s", e)

        as_of = max((p.date for p in merged.values()), default=None)
        return {
            "added": added, "updated": updated, "removed": removed, "total": len(merged),
            "as_of": as_of.isoformat() if as_of else None,
        }

    @_serialized_write
    def write_price_directives(
        self, new_prices: List[Any], remove_keys: Optional[List[Tuple[Any, str, str]]] = None,
    ) -> Dict[str, Any]:
        """The price fetcher's write: merge ``new_prices`` (``data.Price``) into the
        price sidecar, drop ``remove_keys`` ``(date, base, quote)``, rewrite it
        atomically and refresh the mirror. Returns ``{added, updated, removed,
        total, as_of}``.

        The sidecar is deliberately NOT part of any transaction write's
        ``known_files`` (dev-docs/valuations.md §3, Slice 4 gotcha): a price fetch
        never rewrites transaction files, and ``_do_write_entries`` — which
        rewrites *every* known file, blanking those with no entries — never
        touches the sidecar. Prices carry no auto-generated padding, so writing
        them straight through ``BackupManager.atomic_write`` is safe (the
        padding-filter invariant is about transaction rewrites).

        Idempotent: prices are keyed by (date, base, quote); rewriting an existing
        point replaces it in place. A key that also exists in the ledger file is
        skipped (the ledger's price stands). Held under the per-user write lock so
        a fetch and a transaction edit can't interleave.
        """
        return self._apply_price_changes(list(new_prices), list(remove_keys or []), strict=False)

    @_serialized_write
    def edit_price_directives(
        self, set_prices: List[Any], remove_keys: List[Tuple[Any, str, str]],
        keep_existing: Optional[set] = None,
    ) -> Dict[str, Any]:
        """A user's batch of price edits (the Prices tab): set (add or replace by
        ``(date, base, quote)``) and remove sidecar prices in **one** write.
        Rejects (422 ``PRICE_IN_LEDGER``) any key that is a price in the ledger
        file, (404 ``PRICE_NOT_FOUND``) removing a key the sidecar lacks, and
        (409 ``PRICE_EXISTS``) setting a ``keep_existing`` key that is taken.
        See dev-docs/investment-view.md §10.2 (P-Q1, P-Q2, P-Q4)."""
        return self._apply_price_changes(
            list(set_prices), list(remove_keys), strict=True, keep_existing=keep_existing,
        )

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
        remove_metadata: Optional[List[str]] = None,
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
            remove_metadata=remove_metadata,
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
    def upsert_commodity_metadata(self, changes: List[CommodityMetadataChange]) -> List[str]:
        """Apply a batch of commodity metadata changes in **one** write.

        Each change sets/removes keys on the commodity's `commodity` directive,
        creating the directive when the commodity has none (dated the
        commodity's first use, else today). Keys not named are kept. Returns
        the codes whose directive actually changed; no write happens when
        nothing changed. See dev-docs/investment-view.md §10.3 (Q2, Q5, Q7).
        """
        entries, errors, options = self._parse_ledger()

        first_used: Dict[str, date] = {}
        for e in entries:
            if isinstance(e, Transaction):
                for p in e.postings:
                    if p.units is not None:
                        code = p.units.currency
                        if code not in first_used or e.date < first_used[code]:
                            first_used[code] = e.date

        new_entries = list(entries)
        changed: List[str] = []
        for change in changes:
            typed = {
                k: to_ledger_value((SCOPE_COMMODITY,), k, v)
                for k, v in change.set.items()
            }
            new_entries, did_change = self.engine.upsert_commodity_metadata(
                new_entries,
                code=change.code,
                set_metadata=typed,
                remove_keys=list(change.remove),
                on_date=first_used.get(change.code),
                ledger_file=str(self.ledger_file),
            )
            if did_change and change.code not in changed:
                changed.append(change.code)

        if changed:
            self._write_and_export(new_entries, errors, options)
        return changed

    @_serialized_write
    def set_operating_currencies(self, currencies: List[str]) -> List[str]:
        """Replace the ledger's ``operating_currency`` option.

        Operating currencies are the authoritative whitelist of commodities
        that play a currency (unit-of-account) role. They live in the ledger —
        the single source of truth — as ``option "operating_currency"`` lines
        in the root file (Beancount only honours options in the top-level
        file). Passing an empty list clears the whitelist, reverting to
        asset-class-based fallback classification. The classification itself is
        derived downstream at export time; see the exporter's
        ``_classify_is_currency`` and dev-docs/commodities-and-currencies.md.
        """
        # Dedupe while preserving order — the option is a list, not a set.
        deduped: List[str] = []
        for code in currencies:
            if code not in deduped:
                deduped.append(code)

        entries, errors, options = self._parse_ledger()
        options = dict(options)
        options['operating_currency'] = deduped
        self._write_and_export(entries, errors, options)
        return deduped

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
