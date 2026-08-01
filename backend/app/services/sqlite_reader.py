"""
SqliteReader — read-only access to the materialized ledger state in SQLite.

Replaces LedgerCache for all read operations. Holds no data in memory —
each call opens a connection, checks freshness, queries, and returns.

The _query() method bundles the freshness check and connection management
so that developers adding new read methods can't forget either one.
"""

import os
import json
import logging
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from app.schemas.account_schemas import AccountCurrencyData, AccountDetails, BalanceDirectiveData
from app.schemas.commodity_schemas import CommodityDetails, CommodityUsageData

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SqliteReader:
    """Read-only query layer over the expanded SQLite schema.

    Replaces LedgerCache for all read operations in the CQRS model.
    """

    def __init__(
        self,
        sqlite_path: Path,
        ledger_file: Path,
        exporter: Any,  # SQLiteExporter — forward ref to avoid circular import
        write_lock: Optional[Any] = None,  # WriteLockManager — forward ref
    ):
        self.sqlite_path = sqlite_path
        self.ledger_file = ledger_file
        self.exporter = exporter
        # Optional so unit tests can construct a reader without wiring a lock.
        # Production wiring (`service_factory`) always passes the per-user lock
        # so stale-recovery serialises against writes and other readers.
        self.write_lock = write_lock

    # ── Core query infrastructure ────────────────────────────────────────────

    def _needs_export(self) -> bool:
        """The single freshness gate — cheap, no side effects, no hashing.

        Rebuild the mirror iff any of:
          * the DB file is missing;
          * its ``meta`` row says the build didn't complete, or is absent
            (a legacy/partial DB);
          * the recorded ``export_version`` differs from the current one
            (schema or export-logic changed — see EXPORT_VERSION);
          * any recorded ledger file's (mtime_ns, size) no longer matches
            (the ledger changed, including any ``include``d file).

        When no file fingerprint was recorded (e.g. an export that wasn't given
        the ledger path), fall back to the historical root-mtime-vs-DB check.
        Cost on the fast path: one connection + a one-row read + a few stat()s.
        """
        from app.services.sqlite_exporter import EXPORT_VERSION

        if not self.sqlite_path.exists():
            return True

        try:
            con = sqlite3.connect(str(self.sqlite_path))
            con.execute("PRAGMA query_only = ON")  # read-only probe, consistent with _query
            try:
                meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            except sqlite3.OperationalError:
                return True  # no meta table → legacy/partial mirror
            finally:
                con.close()
        except Exception:
            return True

        if meta.get("build_complete") != "1":
            return True
        if meta.get("export_version") != EXPORT_VERSION:
            return True

        files_json = meta.get("ledger_files")
        if not files_json:
            # No recorded fingerprint — fall back to the old root-mtime check.
            try:
                return self.ledger_file.stat().st_mtime > self.sqlite_path.stat().st_mtime
            except FileNotFoundError:
                return True

        try:
            recorded = json.loads(files_json)
        except (ValueError, TypeError):
            return True
        for entry in recorded:
            try:
                path_str, mtime_ns, size = entry
                st = os.stat(path_str)
            except (OSError, ValueError, TypeError):
                return True
            if st.st_mtime_ns != mtime_ns or st.st_size != size:
                return True
        return False

    def ensure_fresh(self) -> bool:
        """Public entry point for the freshness gate + locked recovery.

        Used at service startup (`startup_user_services`) to rebuild the mirror
        only when stale, instead of unconditionally. Reads go through
        ``_ensure_fresh`` via ``_query``. Returns ``True`` if a rebuild was
        performed, ``False`` if the mirror was already fresh.
        """
        return self._ensure_fresh()

    def _ensure_fresh(self) -> bool:
        """Detect and recover from stale SQLite — any cause.

        Catches: failed exports, external edits to the .beancount file,
        crash between write and export, manual file manipulation, schema/export
        drift, and missing CQRS tables. Fast path (already fresh): one
        connection + a one-row ``meta`` read + a few stat()s (see
        ``_needs_export``); no hashing.

        Returns ``True`` if a rebuild was performed, ``False`` if the mirror was
        already fresh — so callers (e.g. startup) can log which happened.

        On a miss we acquire the per-user write lock before re-exporting so
        that (a) concurrent stale-detect readers serialise instead of all
        re-parsing the ledger in parallel, and (b) the re-export can't
        interleave with an in-flight ``LedgerManager`` write. Inside the
        lock we re-check freshness — another thread may have already done
        the work while we were blocked.
        """
        if not self._needs_export():
            return False

        # No lock wired (test-only construction): preserve the historical
        # unlocked behaviour. Logged once so it's diagnosable.
        if self.write_lock is None:
            logger.warning(
                "SQLite stale and no write_lock wired — re-exporting without "
                "serialisation; this is only safe in single-threaded tests"
            )
            self._do_recovery_export()
            return True

        with self.write_lock.acquire("sqlite_stale_recovery"):
            # Double-check inside the lock: another thread may have just
            # finished the same recovery.
            if not self._needs_export():
                return False
            self._do_recovery_export()
            return True

    def _do_recovery_export(self) -> None:
        logger.warning("SQLite stale or missing tables — triggering full re-export")
        from app.core.ledger_loader import load_ledger_checked
        entries, errors, options = load_ledger_checked(self.ledger_file)
        self.exporter.export_full_sync(
            entries, errors, options, ledger_file=str(self.ledger_file)
        )

    def _query(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """All reads go through this — freshness check + connection automatic.

        Developers adding new read methods use _query() to get their
        connection, so there's no way to forget the freshness check.

        The connection is opened read-only (`PRAGMA query_only = ON`): every read
        method — and every consumer handed this reader, notably the compute
        registry (§3.6 G3) — is prevented from writing the mirror by the SQLite
        engine itself, not merely by convention. Stale-recovery re-exports run
        earlier through the exporter's own write connection (see `_ensure_fresh`),
        so this never blocks a legitimate re-export.
        """
        self._ensure_fresh()
        con = sqlite3.connect(str(self.sqlite_path))
        con.execute("PRAGMA query_only = ON")
        con.row_factory = sqlite3.Row
        try:
            return fn(con)
        finally:
            con.close()

    # ── Account reads ────────────────────────────────────────────────────────

    def get_accounts(self) -> List[AccountDetails]:
        """Get all accounts with balances (replaces LedgerCache.get_accounts)."""
        def query(con: sqlite3.Connection) -> List[AccountDetails]:
            acct_rows = con.execute(
                "SELECT name, open_date, close_date, currencies_json, metadata_json "
                "FROM accounts"
            ).fetchall()

            balance_rows = con.execute(
                "SELECT account, currency, balance, transaction_count, last_transaction_date "
                "FROM account_balances"
            ).fetchall()

            # Group balances by account
            balances_by_acct: Dict[str, List[AccountCurrencyData]] = {}
            for br in balance_rows:
                acct = br["account"]
                if acct not in balances_by_acct:
                    balances_by_acct[acct] = []
                balances_by_acct[acct].append(AccountCurrencyData(
                    currency=br["currency"],
                    balance=Decimal(br["balance"]),
                    transaction_count=br["transaction_count"] or 0,
                    last_transaction_date=(
                        date.fromisoformat(br["last_transaction_date"])
                        if br["last_transaction_date"] else None
                    ),
                ))

            result = []
            for row in acct_rows:
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                declared = json.loads(row["currencies_json"]) if row["currencies_json"] else []
                result.append(AccountDetails(
                    name=row["name"],
                    open_date=date.fromisoformat(row["open_date"]),
                    close_date=(
                        date.fromisoformat(row["close_date"])
                        if row["close_date"] else None
                    ),
                    currencies=balances_by_acct.get(row["name"], []),
                    declared_currencies=declared,
                    metadata=metadata,
                ))
            return result

        return self._query(query)

    def get_accounts_dict(self) -> Dict[str, AccountDetails]:
        """Get all accounts as a dict keyed by name."""
        return {a.name: a for a in self.get_accounts()}

    def get_account_names(self) -> Set[str]:
        """Get set of all account names (O(1) membership testing)."""
        return self._query(
            lambda con: {r[0] for r in con.execute("SELECT name FROM accounts")}
        )

    def get_account(self, name: str) -> Optional[AccountDetails]:
        """Get a single account by name."""
        def query(con: sqlite3.Connection) -> Optional[AccountDetails]:
            row = con.execute(
                "SELECT name, open_date, close_date, currencies_json, metadata_json "
                "FROM accounts WHERE name = ?",
                (name,),
            ).fetchone()
            if not row:
                return None

            balance_rows = con.execute(
                "SELECT currency, balance, transaction_count, last_transaction_date "
                "FROM account_balances WHERE account = ?",
                (name,),
            ).fetchall()

            currencies = [
                AccountCurrencyData(
                    currency=br["currency"],
                    balance=Decimal(br["balance"]),
                    transaction_count=br["transaction_count"] or 0,
                    last_transaction_date=(
                        date.fromisoformat(br["last_transaction_date"])
                        if br["last_transaction_date"] else None
                    ),
                )
                for br in balance_rows
            ]

            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            return AccountDetails(
                name=row["name"],
                open_date=date.fromisoformat(row["open_date"]),
                close_date=(
                    date.fromisoformat(row["close_date"])
                    if row["close_date"] else None
                ),
                currencies=currencies,
                metadata=metadata,
            )

        return self._query(query)

    def get_accounts_filtered(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[AccountDetails]:
        """Get accounts with date-filtered balances.

        - Income/Expenses: sum postings within [start_date, end_date]
        - Assets/Liabilities: sum ALL postings up to end_date
        """
        if not start_date and not end_date:
            return self.get_accounts()

        effective_end = end_date or date.today()

        def query(con: sqlite3.Connection) -> List[AccountDetails]:
            acct_rows = con.execute(
                "SELECT name, open_date, close_date, currencies_json, metadata_json "
                "FROM accounts"
            ).fetchall()

            # Assets/Liabilities: cumulative up to end_date
            # intentional aggregation-time float; see dev-docs/money-types.md
            bs_rows = con.execute(
                "SELECT account, currency, SUM(CAST(amount AS REAL)) AS balance, "
                "COUNT(*) AS txn_count, MAX(transaction_date) AS last_date "
                "FROM postings "
                "WHERE account_type IN ('Assets', 'Liabilities') "
                "AND transaction_date <= ? "
                "GROUP BY account, currency",
                (effective_end.isoformat(),),
            ).fetchall()

            # Income/Expenses: period total
            # intentional aggregation-time float; see dev-docs/money-types.md
            is_params: list = []
            is_sql = (
                "SELECT account, currency, SUM(CAST(amount AS REAL)) AS balance, "
                "COUNT(*) AS txn_count, MAX(transaction_date) AS last_date "
                "FROM postings "
                "WHERE account_type IN ('Income', 'Expenses', 'Equity') "
                "AND transaction_date <= ? "
            )
            is_params.append(effective_end.isoformat())
            if start_date:
                is_sql += "AND transaction_date >= ? "
                is_params.append(start_date.isoformat())
            is_sql += "GROUP BY account, currency"
            is_rows = con.execute(is_sql, is_params).fetchall()

            # Merge into per-account currency data
            balances_by_acct: Dict[str, List[AccountCurrencyData]] = {}
            for br in list(bs_rows) + list(is_rows):
                acct = br["account"]
                if acct not in balances_by_acct:
                    balances_by_acct[acct] = []
                balances_by_acct[acct].append(AccountCurrencyData(
                    currency=br["currency"],
                    balance=Decimal(str(br["balance"])) if br["balance"] is not None else Decimal("0"),
                    transaction_count=br["txn_count"] or 0,
                    last_transaction_date=(
                        date.fromisoformat(br["last_date"]) if br["last_date"] else None
                    ),
                ))

            result = []
            for row in acct_rows:
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                declared = json.loads(row["currencies_json"]) if row["currencies_json"] else []
                result.append(AccountDetails(
                    name=row["name"],
                    open_date=date.fromisoformat(row["open_date"]),
                    close_date=(
                        date.fromisoformat(row["close_date"])
                        if row["close_date"] else None
                    ),
                    currencies=balances_by_acct.get(row["name"], []),
                    declared_currencies=declared,
                    metadata=metadata,
                ))
            return result

        return self._query(query)

    # ── Commodity reads ──────────────────────────────────────────────────────

    def get_commodities(self) -> List[CommodityDetails]:
        """Get all commodities with role classification and usage stats.

        The ``commodities`` table is exported complete (declared ∪ used), so
        every commodity has exactly one row here — no separate merge with
        usage-only codes is needed. See the exporter's ``_export_commodities``.
        """
        def query(con: sqlite3.Connection) -> List[CommodityDetails]:
            comm_rows = con.execute(
                "SELECT code, declaration_date, name, asset_class, is_currency, "
                "metadata_json FROM commodities"
            ).fetchall()

            usage_rows = con.execute(
                "SELECT code, transaction_count, total_volume, first_seen, last_seen "
                "FROM commodity_usage"
            ).fetchall()
            usage_by_code = {r["code"]: r for r in usage_rows}

            result = []
            for row in comm_rows:
                u = usage_by_code.get(row["code"])
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                first_seen = (
                    date.fromisoformat(row["declaration_date"])
                    if row["declaration_date"] else None
                )
                last_seen = first_seen
                usage = CommodityUsageData(transaction_count=0, total_volume=Decimal("0"))
                if u:
                    usage = CommodityUsageData(
                        transaction_count=u["transaction_count"],
                        total_volume=Decimal(u["total_volume"]),
                    )
                    if u["first_seen"]:
                        fs = date.fromisoformat(u["first_seen"])
                        if first_seen is None or fs < first_seen:
                            first_seen = fs
                    if u["last_seen"]:
                        ls = date.fromisoformat(u["last_seen"])
                        if last_seen is None or ls > last_seen:
                            last_seen = ls

                result.append(CommodityDetails(
                    code=row["code"],
                    name=row["name"],
                    asset_class=row["asset_class"],
                    is_currency=bool(row["is_currency"]),
                    first_seen=first_seen,
                    last_seen=last_seen,
                    usage=usage,
                    metadata=metadata,
                ))

            return result

        return self._query(query)

    def get_commodity_codes(self) -> Set[str]:
        """Get set of all commodity codes."""
        def query(con: sqlite3.Connection) -> Set[str]:
            declared = {r[0] for r in con.execute("SELECT code FROM commodities")}
            from_usage = {r[0] for r in con.execute("SELECT code FROM commodity_usage")}
            return declared | from_usage
        return self._query(query)

    # ── Transaction reads ────────────────────────────────────────────────────

    def get_transaction_ids(self) -> Set[str]:
        """Get set of all transaction IDs from the postings table."""
        return self._query(
            lambda con: {
                r[0] for r in con.execute(
                    "SELECT DISTINCT transaction_id FROM postings"
                )
            }
        )

    def get_transactions(self) -> list:
        """Get simplified transactions for duplicate detection.

        Returns list of dicts matching the LedgerTransaction fields.
        """
        from app.core.ledger_cache import LedgerTransaction
        from decimal import Decimal

        def query(con: sqlite3.Connection) -> list:
            # Group postings by transaction_id, take the source_account posting
            rows = con.execute("""
                SELECT DISTINCT
                    p.transaction_id,
                    p.transaction_content_hash,
                    p.transaction_date,
                    p.transaction_payee,
                    p.transaction_narration,
                    p.amount,
                    p.account,
                    p.source_account,
                    p.transaction_metadata_json
                FROM postings p
                WHERE p.account_type IN ('Assets', 'Liabilities')
                ORDER BY p.transaction_date
            """).fetchall()

            # Deduplicate by transaction_id, keeping first (source account) row
            seen: Dict[str, bool] = {}
            result = []
            for r in rows:
                tid = r["transaction_id"]
                if tid in seen:
                    continue
                seen[tid] = True

                meta = json.loads(r["transaction_metadata_json"]) if r["transaction_metadata_json"] else {}
                external_id = meta.get("external_id") or meta.get("ofx_id")
                external_id_type = meta.get("external_id_type") or ("OFX" if meta.get("ofx_id") else None)

                result.append(LedgerTransaction(
                    id=tid,
                    content_hash=r["transaction_content_hash"] or "",
                    date=date.fromisoformat(r["transaction_date"]),
                    payee=r["transaction_payee"] or "",
                    narration=r["transaction_narration"] or "",
                    amount=Decimal(r["amount"]) if r["amount"] is not None else Decimal("0"),
                    account=r["source_account"] or r["account"],
                    external_id=external_id,
                    external_id_type=external_id_type,
                ))
            return result

        return self._query(query)

    # ── Training data ────────────────────────────────────────────────────────

    def get_training_data(self) -> List[Tuple[str, str]]:
        """Get training data for ML categorization."""
        return self._query(
            lambda con: [
                (r[0], r[1])
                for r in con.execute("SELECT description, category FROM training_data")
            ]
        )

    # ── Errors ───────────────────────────────────────────────────────────────

    def get_errors(self) -> List[dict]:
        """Get Beancount parsing errors from last export."""
        return self._query(
            lambda con: [dict(r) for r in con.execute("SELECT * FROM ledger_errors")]
        )

    def has_errors(self) -> bool:
        """Check if there are any parsing errors."""
        return self._query(
            lambda con: con.execute(
                "SELECT COUNT(*) FROM ledger_errors"
            ).fetchone()[0] > 0
        )

    # ── Balance directives ───────────────────────────────────────────────────

    def get_balance_directives(self, account_name: str) -> List[BalanceDirectiveData]:
        """Get balance assertions for an account, with their paired pads.

        Pad pairing is stamped on each balance row at export time using
        Beancount's "most recent unused pad" semantics (see
        ``_export_full_ledger`` and ``engine._find_pad_before_balance_entry``).
        The reader just selects the pre-paired columns.
        """
        def query(con: sqlite3.Connection) -> List[BalanceDirectiveData]:
            errors = con.execute(
                "SELECT line_number, message FROM ledger_errors "
                "WHERE message LIKE '%balance%'"
            ).fetchall()
            error_by_line = {r["line_number"]: r["message"] for r in errors if r["line_number"]}

            balances = con.execute(
                "SELECT date, amount_number, amount_currency, passed, "
                "diff_number, diff_currency, metadata_json, "
                "pad_date, pad_source_account "
                "FROM balance_assertions WHERE account = ? ORDER BY date",
                (account_name,),
            ).fetchall()

            result = []
            for bal in balances:
                meta = json.loads(bal["metadata_json"]) if bal["metadata_json"] else {}
                lineno = meta.get("lineno", 0)
                has_error = bal["passed"] == 0 or lineno in error_by_line
                error_message = error_by_line.get(lineno)
                pad_source = bal["pad_source_account"]

                result.append(BalanceDirectiveData(
                    date=date.fromisoformat(bal["date"]),
                    currency=bal["amount_currency"],
                    expected_balance=Decimal(bal["amount_number"]),
                    has_pad=pad_source is not None,
                    pad_source_account=pad_source,
                    has_error=has_error,
                    error_message=error_message,
                ))
            return result

        return self._query(query)

    def get_custom_directives(self, directive_type: str) -> List[dict]:
        """Return raw `custom` directives of a given type (e.g. 'budget'),
        ordered by (date, source line) for deterministic last-wins resolution.

        Each row: {date, values_json, source_file, source_lineno}. Parsing the
        type-specific `values_json` shape is the caller's concern (e.g. the
        budget resolver). Read-only.
        """
        def query(con: sqlite3.Connection) -> List[dict]:
            rows = con.execute(
                "SELECT date, values_json, metadata_json FROM custom_directives "
                "WHERE type = ? ORDER BY date",
                (directive_type,),
            ).fetchall()
            result = []
            for r in rows:
                meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                result.append({
                    "date": r["date"],
                    "values_json": r["values_json"],
                    "source_file": meta.get("filename"),
                    "source_lineno": meta.get("lineno", 0),
                })
            # Stable secondary sort by source line for same-date last-wins (§4.3).
            result.sort(key=lambda x: (x["date"], x["source_file"] or "", x["source_lineno"]))
            return result

        return self._query(query)

    # ── Documents (account-level) ─────────────────────────────────────────────

    def get_documents(self, account: Optional[str] = None) -> list:
        """Account documents (``Document`` directives), optionally filtered.

        Beancount absolutizes ``Document`` filenames on load, so the stored
        ``filename`` is absolute; this converts it back to the ledger-relative
        form the serve endpoint expects and exposes a display basename.
        """
        import os
        from app.schemas.document_schemas import DocumentDetails

        ledger_dir = str(Path(self.ledger_file).resolve().parent)

        def query(con: sqlite3.Connection) -> list:
            if account:
                rows = con.execute(
                    "SELECT date, account, filename, tags_json, links_json, metadata_json "
                    "FROM documents WHERE account = ? ORDER BY date DESC, filename",
                    (account,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT date, account, filename, tags_json, links_json, metadata_json "
                    "FROM documents ORDER BY date DESC, filename"
                ).fetchall()

            result = []
            for r in rows:
                filename = r["filename"]
                if os.path.isabs(filename):
                    # Resolve both sides so a symlinked path component (e.g.
                    # /tmp → /private/tmp on macOS) can't make relpath diverge
                    # from how the serve endpoint resolves the same path.
                    rel = os.path.relpath(
                        str(Path(filename).resolve()), ledger_dir
                    ).replace(os.sep, "/")
                else:
                    rel = filename
                tags = json.loads(r["tags_json"]) if r["tags_json"] else []
                links = json.loads(r["links_json"]) if r["links_json"] else []
                metadata = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                result.append(DocumentDetails(
                    date=date.fromisoformat(r["date"]),
                    account=r["account"],
                    path=rel,
                    display_name=os.path.basename(filename),
                    tags=tags,
                    links=links,
                    metadata=metadata,
                ))
            return result

        return self._query(query)

    # ── Options ──────────────────────────────────────────────────────────────

    def get_options(self) -> Dict[str, Any]:
        """Get Beancount options from the ledger_options table."""
        def query(con: sqlite3.Connection) -> Dict[str, Any]:
            rows = con.execute("SELECT key, value_json FROM ledger_options").fetchall()
            return {r["key"]: json.loads(r["value_json"]) for r in rows}
        return self._query(query)

    def get_operating_currencies(self) -> List[str]:
        """Return the ledger's declared operating currencies (may be empty).

        Sourced from the ``operating_currency`` option — the authoritative
        currency whitelist. See dev-docs/commodities-and-currencies.md.
        """
        value = self.get_options().get("operating_currency") or []
        return list(value)

    # ── Lots ─────────────────────────────────────────────────────────────────

    def get_lots(self, account: Optional[str] = None) -> List[dict]:
        """Get investment lot positions."""
        def query(con: sqlite3.Connection) -> List[dict]:
            if account:
                rows = con.execute(
                    "SELECT * FROM lots WHERE account = ?", (account,)
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM lots").fetchall()
            return [dict(r) for r in rows]
        return self._query(query)

    # ── Prices ───────────────────────────────────────────────────────────────

    def get_postings_by_currency(self, currencies: List[str]) -> List[dict]:
        """Return posting rows for the given commodity ``currencies``, ordered by
        date, each as ``{transaction_date, currency, amount, cost_amount,
        cost_currency, account}``.

        This is the raw material for reconstructing a holding's **units-as-of** a
        date (cumulative ``amount``) and its **cost-basis-as-of** (cumulative
        ``amount × cost_amount``) — the inputs the ``portfolio_series`` valuation
        compute function needs. Money columns stay TEXT/decimal-string; the caller
        parses to ``Decimal`` (money-types.md). An empty ``currencies`` returns
        ``[]`` without a query.
        """
        if not currencies:
            return []

        def query(con: sqlite3.Connection) -> List[dict]:
            placeholders = ",".join("?" for _ in currencies)
            rows = con.execute(
                f"SELECT transaction_date, currency, amount, cost_amount, "
                f"cost_currency, account FROM postings "
                f"WHERE currency IN ({placeholders}) "
                f"ORDER BY transaction_date, posting_id",
                list(currencies),
            ).fetchall()
            return [dict(r) for r in rows]

        return self._query(query)

    def get_investment_cashflow_postings(self, holdings: List[str]) -> List[dict]:
        """Return **all legs** of every transaction that either touches an
        investment holding or books to an ``Income:Dividends*`` account, each as
        ``{transaction_id, transaction_date, account, account_type, currency,
        amount, cost_amount, price_amount}`` ordered by date.

        This is the raw material for portfolio **XIRR** (``portfolio_returns``):
        from these legs the caller extracts the external cash flows — the cash
        (currency) Asset/Liability legs of investment transactions (a buy's cash
        out, a sale's proceeds, a cash dividend in; a reinvested-income DRIP has
        no cash leg and nets to zero). Money columns stay TEXT/decimal-string.
        Empty ``holdings`` still returns dividend transactions.
        """
        def query(con: sqlite3.Connection) -> List[dict]:
            placeholders = ",".join("?" for _ in holdings) if holdings else "NULL"
            rows = con.execute(
                f"SELECT transaction_id, transaction_date, account, account_type, "
                f"currency, amount, cost_amount, price_amount FROM postings "
                f"WHERE transaction_id IN ("
                f"  SELECT DISTINCT transaction_id FROM postings "
                f"  WHERE currency IN ({placeholders}) OR account LIKE 'Income:Dividends%'"
                f") ORDER BY transaction_date, transaction_id, posting_id",
                list(holdings),
            ).fetchall()
            return [dict(r) for r in rows]

        return self._query(query)

    def get_prices(
        self,
        currency: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[dict]:
        """Get price history entries."""
        def query(con: sqlite3.Connection) -> List[dict]:
            sql = "SELECT * FROM prices WHERE 1=1"
            params: list = []
            if currency:
                sql += " AND base_currency = ?"
                params.append(currency)
            if start:
                sql += " AND date >= ?"
                params.append(start)
            if end:
                sql += " AND date <= ?"
                params.append(end)
            sql += " ORDER BY date"
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        return self._query(query)
