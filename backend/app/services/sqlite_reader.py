"""
SqliteReader — read-only access to the materialized ledger state in SQLite.

Replaces LedgerCache for all read operations. Holds no data in memory —
each call opens a connection, checks freshness, queries, and returns.

The _query() method bundles the freshness check and connection management
so that developers adding new read methods can't forget either one.
"""

import json
import logging
import sqlite3
from datetime import date
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
    ):
        self.sqlite_path = sqlite_path
        self.ledger_file = ledger_file
        self.exporter = exporter

    # ── Core query infrastructure ────────────────────────────────────────────

    def _ensure_fresh(self) -> None:
        """Detect and recover from stale SQLite — any cause.

        Catches: failed exports, external edits to the .beancount file,
        crash between write and export, manual file manipulation,
        and missing CQRS tables (legacy export that only created postings).
        Cost: one stat() call per read (~1 microsecond).
        """
        needs_export = False

        try:
            ledger_mtime = self.ledger_file.stat().st_mtime
            sqlite_mtime = self.sqlite_path.stat().st_mtime
            if ledger_mtime > sqlite_mtime:
                needs_export = True
        except FileNotFoundError:
            needs_export = True

        # Also check if new tables exist (handles legacy DBs with only postings)
        if not needs_export and self.sqlite_path.exists():
            try:
                import sqlite3 as _sqlite3
                con = _sqlite3.connect(str(self.sqlite_path))
                row = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
                ).fetchone()
                con.close()
                if row is None:
                    needs_export = True
            except Exception:
                needs_export = True

        if needs_export:
            logger.warning("SQLite stale or missing tables — triggering full re-export")
            from app.core.ledger_loader import load_ledger_checked
            entries, errors, options = load_ledger_checked(self.ledger_file)
            self.exporter._export_full_to_sqlite(entries, errors, options)

    def _query(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """All reads go through this — freshness check + connection automatic.

        Developers adding new read methods use _query() to get their
        connection, so there's no way to forget the freshness check.
        """
        self._ensure_fresh()
        con = sqlite3.connect(str(self.sqlite_path))
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
                    balance=float(br["balance"]),
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
                    balance=float(br["balance"]),
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
            bs_rows = con.execute(
                "SELECT account, currency, SUM(amount) AS balance, "
                "COUNT(*) AS txn_count, MAX(transaction_date) AS last_date "
                "FROM postings "
                "WHERE account_type IN ('Assets', 'Liabilities') "
                "AND transaction_date <= ? "
                "GROUP BY account, currency",
                (effective_end.isoformat(),),
            ).fetchall()

            # Income/Expenses: period total
            is_params: list = []
            is_sql = (
                "SELECT account, currency, SUM(amount) AS balance, "
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
                    balance=float(br["balance"]) if br["balance"] else 0.0,
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
        """Get all commodities with usage stats."""
        def query(con: sqlite3.Connection) -> List[CommodityDetails]:
            comm_rows = con.execute(
                "SELECT code, declaration_date, name, type, metadata_json "
                "FROM commodities"
            ).fetchall()

            usage_rows = con.execute(
                "SELECT code, transaction_count, total_volume, first_seen, last_seen "
                "FROM commodity_usage"
            ).fetchall()
            usage_by_code = {r["code"]: r for r in usage_rows}

            # Also pick up commodities that appear in usage but not in declarations
            declared_codes = {r["code"] for r in comm_rows}
            extra_usage = [u for u in usage_rows if u["code"] not in declared_codes]

            result = []
            for row in comm_rows:
                u = usage_by_code.get(row["code"])
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                first_seen = (
                    date.fromisoformat(row["declaration_date"])
                    if row["declaration_date"] else None
                )
                last_seen = first_seen
                usage = CommodityUsageData(transaction_count=0, total_volume=0.0)
                if u:
                    usage = CommodityUsageData(
                        transaction_count=u["transaction_count"],
                        total_volume=float(u["total_volume"]),
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
                    type=row["type"],
                    first_seen=first_seen,
                    last_seen=last_seen,
                    usage=usage,
                    metadata=metadata,
                ))

            # Commodities from usage only (no declaration directive)
            for u in extra_usage:
                result.append(CommodityDetails(
                    code=u["code"],
                    name=None,
                    type=None,
                    first_seen=(
                        date.fromisoformat(u["first_seen"]) if u["first_seen"] else None
                    ),
                    last_seen=(
                        date.fromisoformat(u["last_seen"]) if u["last_seen"] else None
                    ),
                    usage=CommodityUsageData(
                        transaction_count=u["transaction_count"],
                        total_volume=float(u["total_volume"]),
                    ),
                    metadata={},
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
                    amount=Decimal(str(r["amount"])) if r["amount"] is not None else Decimal(0),
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
        """Get balance assertions for an account, paired with pad directives."""
        def query(con: sqlite3.Connection) -> List[BalanceDirectiveData]:
            errors = con.execute(
                "SELECT line_number, message FROM ledger_errors "
                "WHERE message LIKE '%balance%'"
            ).fetchall()
            error_by_line = {r["line_number"]: r["message"] for r in errors if r["line_number"]}

            pads = con.execute(
                "SELECT date, source_account FROM pad_directives WHERE account = ? ORDER BY date",
                (account_name,),
            ).fetchall()

            balances = con.execute(
                "SELECT date, amount_number, amount_currency, passed, "
                "diff_number, diff_currency, metadata_json "
                "FROM balance_assertions WHERE account = ? ORDER BY date",
                (account_name,),
            ).fetchall()

            pad_iter = iter(pads)
            current_pad = next(pad_iter, None)

            result = []
            for bal in balances:
                bal_date = date.fromisoformat(bal["date"])

                pad_source = None
                if current_pad and date.fromisoformat(current_pad["date"]) <= bal_date:
                    pad_source = current_pad["source_account"]
                    current_pad = next(pad_iter, None)

                meta = json.loads(bal["metadata_json"]) if bal["metadata_json"] else {}
                lineno = meta.get("lineno", 0)
                has_error = bal["passed"] == 0 or lineno in error_by_line
                error_message = error_by_line.get(lineno)

                result.append(BalanceDirectiveData(
                    date=bal_date,
                    currency=bal["amount_currency"],
                    expected_balance=float(bal["amount_number"]),
                    has_pad=pad_source is not None,
                    pad_source_account=pad_source,
                    has_error=has_error,
                    error_message=error_message,
                ))

            result.sort(key=lambda d: d.date)
            return result

        return self._query(query)

    # ── Options ──────────────────────────────────────────────────────────────

    def get_options(self) -> Dict[str, Any]:
        """Get Beancount options from the ledger_options table."""
        def query(con: sqlite3.Connection) -> Dict[str, Any]:
            rows = con.execute("SELECT key, value_json FROM ledger_options").fetchall()
            return {r["key"]: json.loads(r["value_json"]) for r in rows}
        return self._query(query)

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
