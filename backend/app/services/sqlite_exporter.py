"""
SQLite Exporter Service - Converts Beancount entries to SQLite format.

This service is responsible for the actual data conversion (the worker).
It accepts parsed Beancount entries and exports them to a SQLite database with WAL mode.

The exporter populates two groups of tables:
- postings (existing): denormalized analytics table for dashboards/recipes/AI queries
- ledger mirror tables (new): normalized representation of all Beancount directives
  and computed state (accounts, commodities, balances, lots, prices, etc.)
"""
import json
import time
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, date

from app.core.constants import SOURCE_ACCOUNT_TYPES, INCOME_STATEMENT_PREFIXES

from beancount.core.data import Transaction, Posting
from beancount.core import data

logger = logging.getLogger(__name__)


# All tables added by the CQRS expansion (does NOT include 'postings')
_NEW_TABLES = [
    "accounts",
    "commodities",
    "balance_assertions",
    "pad_directives",
    "prices",
    "notes",
    "events",
    "documents",
    "custom_directives",
    "stored_queries",
    "ledger_options",
    "account_balances",
    "lots",
    "commodity_usage",
    "ledger_errors",
    "training_data",
]


class SQLiteExporter:
    """Service for exporting Beancount entries to SQLite format with WAL mode."""

    def __init__(self, sqlite_path: str, enable_wal: bool = True):
        """
        Initialize SQLite exporter.

        Args:
            sqlite_path: Path to the SQLite database file
            enable_wal: Enable WAL mode for concurrent access (default: True)
        """
        self.sqlite_path = sqlite_path
        self.export_path = sqlite_path  # Generic interface
        self.enable_wal = enable_wal

    # ── Public async API ─────────────────────────────────────────────────────

    async def export_entries(
        self,
        entries: List[Any],
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Export Beancount entries to SQLite (postings table only — legacy API).

        Args:
            entries: Parsed Beancount entries from cache
            force: Regenerate even if up-to-date

        Returns:
            Dictionary with success, postings_count, transactions_count, etc.
        """
        start_time = time.time()

        try:
            # Filter for transactions only
            transactions = [e for e in entries if isinstance(e, Transaction)]
            logger.info(f"Exporting {len(transactions)} transactions to SQLite")

            # Run blocking SQLite operations in thread pool
            result = await asyncio.to_thread(
                self._export_transactions_to_sqlite,
                transactions
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": True,
                "postings_count": result["postings_count"],
                "transactions_count": result["transactions_count"],
                "duration_ms": duration_ms,
                "path": str(self.sqlite_path),
                "last_sync": datetime.utcnow().isoformat() + "Z"
            }

        except Exception as e:
            logger.error(f"SQLite export failed: {e}", exc_info=True)
            raise Exception(f"Failed to export to SQLite: {str(e)}")

    async def export_full(
        self,
        entries: List[Any],
        errors: List[Any],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Export ALL Beancount state to SQLite — postings + all ledger mirror tables.

        This is the expanded export used in the CQRS model. It runs the existing
        postings export plus new exports for all directive types and computed state,
        all in a single atomic transaction.

        Args:
            entries: Parsed Beancount entries
            errors: Beancount parsing errors
            options: Beancount options dict

        Returns:
            Dictionary with success, postings_count, duration_ms, etc.
        """
        start_time = time.time()

        try:
            result = await asyncio.to_thread(
                self._export_full_to_sqlite, entries, errors, options
            )
            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": True,
                "postings_count": result["postings_count"],
                "transactions_count": result["transactions_count"],
                "duration_ms": duration_ms,
                "path": str(self.sqlite_path),
                "last_sync": datetime.utcnow().isoformat() + "Z",
            }

        except Exception as e:
            logger.error(f"SQLite full export failed: {e}", exc_info=True)
            raise Exception(f"Failed to full-export to SQLite: {str(e)}")

    async def get_status(self) -> Dict[str, Any]:
        """Get SQLite export status."""
        try:
            db_path = Path(self.sqlite_path)

            if not db_path.exists():
                return {
                    "exists": False,
                    "path": str(self.sqlite_path),
                    "size_bytes": 0,
                    "last_modified": None,
                    "postings_count": 0
                }

            stat = db_path.stat()
            last_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

            postings_count = await asyncio.to_thread(
                self._get_postings_count
            )

            return {
                "exists": True,
                "path": str(self.sqlite_path),
                "size_bytes": stat.st_size,
                "last_modified": last_modified,
                "postings_count": postings_count
            }

        except Exception as e:
            logger.error(f"Failed to get SQLite status: {e}", exc_info=True)
            return {
                "exists": False,
                "path": str(self.sqlite_path),
                "size_bytes": 0,
                "last_modified": None,
                "postings_count": 0,
                "error": str(e)
            }

    def _get_postings_count(self) -> int:
        """Get count of postings in the database (blocking I/O)."""
        try:
            con = sqlite3.connect(self.sqlite_path)
            cursor = con.execute("SELECT COUNT(*) FROM postings")
            result = cursor.fetchone()
            con.close()
            return result[0] if result else 0
        except Exception as e:
            logger.warning(f"Could not get postings count: {e}")
            return 0

    # ── Connection / schema helpers ──────────────────────────────────────────

    def _open_connection(self) -> sqlite3.Connection:
        """Open a connection with standard WAL and performance settings."""
        db_path = Path(self.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        con = sqlite3.connect(
            self.sqlite_path,
            timeout=30.0,
            isolation_level="IMMEDIATE",
        )

        if self.enable_wal:
            con.execute("PRAGMA journal_mode=WAL").fetchone()
            con.execute("PRAGMA synchronous=NORMAL").fetchone()
            con.execute("PRAGMA wal_autocheckpoint=0").fetchone()
            con.execute("PRAGMA cache_size=10000").fetchone()
            con.execute("PRAGMA temp_store=MEMORY").fetchone()

        return con

    def _ensure_postings_table(self, con: sqlite3.Connection) -> None:
        """Create the postings table and indexes if they don't exist."""
        cursor = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='postings'"
        )
        if cursor.fetchone() is not None:
            return

        con.execute("""
            CREATE TABLE postings (
                posting_id INTEGER PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                transaction_content_hash TEXT,
                transaction_date TEXT NOT NULL,
                transaction_flag TEXT,
                transaction_payee TEXT,
                transaction_narration TEXT,
                transaction_tags TEXT,
                transaction_links TEXT,
                account TEXT NOT NULL,
                account_type TEXT NOT NULL,
                amount REAL,
                currency TEXT,
                cost_amount REAL,
                cost_currency TEXT,
                price_amount REAL,
                price_currency TEXT,
                source_account TEXT,
                source_account_type TEXT,
                transaction_metadata_json TEXT,
                posting_metadata_json TEXT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                quarter INTEGER NOT NULL,
                year_month TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX idx_transaction_date ON postings(transaction_date)")
        con.execute("CREATE INDEX idx_account ON postings(account)")
        con.execute("CREATE INDEX idx_account_type ON postings(account_type)")
        con.execute("CREATE INDEX idx_year_month ON postings(year_month)")
        con.execute("CREATE INDEX idx_transaction_id ON postings(transaction_id)")
        con.execute("CREATE INDEX idx_content_hash ON postings(transaction_content_hash)")
        logger.info("Created postings table and indexes")

    def _ensure_new_tables(self, con: sqlite3.Connection) -> None:
        """Create all CQRS ledger-mirror tables if they don't exist."""
        cursor = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        )
        if cursor.fetchone() is not None:
            return  # Already created

        # ── Non-transaction directive tables ─────────────────────────────
        con.execute("""
            CREATE TABLE accounts (
                name            TEXT PRIMARY KEY,
                open_date       TEXT NOT NULL,
                close_date      TEXT,
                currencies_json TEXT,
                booking         TEXT,
                metadata_json   TEXT
            )
        """)
        con.execute(
            "CREATE INDEX idx_accounts_type "
            "ON accounts(substr(name, 1, instr(name, ':') - 1))"
        )

        con.execute("""
            CREATE TABLE commodities (
                code            TEXT PRIMARY KEY,
                declaration_date TEXT,
                name            TEXT,
                type            TEXT,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE balance_assertions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                account         TEXT NOT NULL,
                amount_number   TEXT NOT NULL,
                amount_currency TEXT NOT NULL,
                tolerance       TEXT,
                passed          INTEGER NOT NULL DEFAULT 1,
                diff_number     TEXT,
                diff_currency   TEXT,
                metadata_json   TEXT
            )
        """)
        con.execute(
            "CREATE INDEX idx_balance_assertions_account "
            "ON balance_assertions(account, date)"
        )

        con.execute("""
            CREATE TABLE pad_directives (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                account         TEXT NOT NULL,
                source_account  TEXT NOT NULL,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE prices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                base_currency   TEXT NOT NULL,
                quote_number    TEXT NOT NULL,
                quote_currency  TEXT NOT NULL,
                metadata_json   TEXT
            )
        """)
        con.execute("CREATE INDEX idx_prices_base_date ON prices(base_currency, date)")
        con.execute("CREATE INDEX idx_prices_date ON prices(date)")

        con.execute("""
            CREATE TABLE notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                account         TEXT NOT NULL,
                comment         TEXT NOT NULL,
                tags_json       TEXT,
                links_json      TEXT,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                type            TEXT NOT NULL,
                description     TEXT NOT NULL,
                metadata_json   TEXT
            )
        """)
        con.execute("CREATE INDEX idx_events_type ON events(type, date)")

        con.execute("""
            CREATE TABLE documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                account         TEXT NOT NULL,
                filename        TEXT NOT NULL,
                tags_json       TEXT,
                links_json      TEXT,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE custom_directives (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                type            TEXT NOT NULL,
                values_json     TEXT,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE stored_queries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                name            TEXT NOT NULL,
                query_string    TEXT NOT NULL,
                metadata_json   TEXT
            )
        """)

        con.execute("""
            CREATE TABLE ledger_options (
                key             TEXT PRIMARY KEY,
                value_json      TEXT NOT NULL
            )
        """)

        # ── Computed state tables ────────────────────────────────────────
        con.execute("""
            CREATE TABLE account_balances (
                account         TEXT NOT NULL,
                currency        TEXT NOT NULL,
                balance         TEXT NOT NULL,
                transaction_count INTEGER DEFAULT 0,
                last_transaction_date TEXT,
                PRIMARY KEY (account, currency)
            )
        """)
        con.execute(
            "CREATE INDEX idx_account_balances_type "
            "ON account_balances(substr(account, 1, instr(account, ':') - 1))"
        )

        con.execute("""
            CREATE TABLE lots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account         TEXT NOT NULL,
                units_number    TEXT NOT NULL,
                units_currency  TEXT NOT NULL,
                cost_number     TEXT NOT NULL,
                cost_currency   TEXT NOT NULL,
                acquisition_date TEXT NOT NULL,
                label           TEXT,
                book_value      TEXT,
                UNIQUE(account, units_currency, cost_number, cost_currency, acquisition_date)
            )
        """)
        con.execute("CREATE INDEX idx_lots_account ON lots(account)")
        con.execute("CREATE INDEX idx_lots_currency ON lots(units_currency)")

        con.execute("""
            CREATE TABLE commodity_usage (
                code            TEXT PRIMARY KEY,
                transaction_count INTEGER DEFAULT 0,
                total_volume    TEXT DEFAULT '0',
                first_seen      TEXT,
                last_seen       TEXT
            )
        """)

        con.execute("""
            CREATE TABLE ledger_errors (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file     TEXT,
                line_number     INTEGER,
                message         TEXT NOT NULL,
                entry_json      TEXT
            )
        """)

        con.execute("""
            CREATE TABLE training_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                description     TEXT NOT NULL,
                category        TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX idx_training_description ON training_data(description)")

        logger.info("Created CQRS ledger-mirror tables and indexes")

    # ── Postings export (existing logic, unchanged) ──────────────────────────

    def _export_postings(
        self,
        con: sqlite3.Connection,
        transactions: List[Transaction],
    ) -> int:
        """Export transactions to the postings table. Returns postings count."""
        con.execute("DELETE FROM postings")

        posting_id = 0
        rows = []

        for txn_index, txn in enumerate(transactions):
            transaction_id = self._get_transaction_id(txn, txn_index)
            transaction_content_hash = self._get_content_hash(txn)
            transaction_tags = self._serialize_array(self._extract_tags(txn))
            transaction_links = self._serialize_array(self._extract_links(txn))
            transaction_metadata = self._metadata_to_json(txn.meta)

            for posting in txn.postings:
                posting_id += 1

                source_account, source_account_type = self._compute_source_account(txn, posting)
                posting_metadata = self._metadata_to_json(posting.meta) if posting.meta else None

                cost_amount = None
                cost_currency = None
                if posting.cost:
                    cost_number = getattr(posting.cost, 'number', None)
                    if cost_number is not None:
                        cost_amount = self._decimal_to_float(cost_number)
                    cost_currency = getattr(posting.cost, 'currency', None)

                price_amount = None
                price_currency = None
                if posting.price:
                    price_amount = self._decimal_to_float(posting.price.number)
                    price_currency = posting.price.currency

                year = txn.date.year
                month = txn.date.month
                quarter = (month - 1) // 3 + 1
                year_month = f"{year}-{month:02d}"

                rows.append((
                    posting_id,
                    transaction_id,
                    transaction_content_hash,
                    txn.date.isoformat(),
                    txn.flag,
                    txn.payee,
                    txn.narration,
                    transaction_tags,
                    transaction_links,
                    posting.account,
                    self._get_account_type(posting.account),
                    self._decimal_to_float(posting.units.number) if posting.units else None,
                    posting.units.currency if posting.units else None,
                    cost_amount,
                    cost_currency,
                    price_amount,
                    price_currency,
                    source_account,
                    source_account_type,
                    transaction_metadata,
                    posting_metadata,
                    year,
                    month,
                    quarter,
                    year_month,
                ))

        con.executemany(
            "INSERT INTO postings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return posting_id

    # ── Full ledger export (new CQRS tables) ─────────────────────────────────

    def _export_full_ledger(
        self,
        con: sqlite3.Connection,
        entries: List[Any],
        errors: List[Any],
        options: Dict[str, Any],
    ) -> None:
        """Export all directive types and computed state to new tables."""
        # Clear all new tables
        for table in _NEW_TABLES:
            con.execute(f"DELETE FROM {table}")

        # ── 1. Classify entries by type ──────────────────────────────────
        account_rows = []
        close_updates = []       # (close_date, account_name)
        commodity_rows = []
        balance_rows = []
        pad_rows = []
        price_rows = []
        note_rows = []
        event_rows = []
        document_rows = []
        custom_rows = []
        query_rows = []

        for entry in entries:
            if isinstance(entry, data.Open):
                currencies = list(entry.currencies) if entry.currencies else []
                account_rows.append((
                    entry.account,
                    entry.date.isoformat(),
                    None,  # close_date filled by Close directives
                    json.dumps(currencies),
                    getattr(entry, 'booking', None),
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Close):
                close_updates.append((entry.date.isoformat(), entry.account))

            elif isinstance(entry, data.Commodity):
                meta = entry.meta or {}
                commodity_rows.append((
                    entry.currency,
                    entry.date.isoformat(),
                    meta.get('name'),
                    meta.get('type'),
                    self._metadata_to_json(meta),
                ))

            elif isinstance(entry, data.Balance):
                passed = 1 if entry.diff_amount is None else 0
                diff_num = str(entry.diff_amount.number) if entry.diff_amount else None
                diff_ccy = entry.diff_amount.currency if entry.diff_amount else None
                balance_rows.append((
                    entry.date.isoformat(),
                    entry.account,
                    str(entry.amount.number),
                    entry.amount.currency,
                    str(entry.tolerance) if getattr(entry, 'tolerance', None) is not None else None,
                    passed,
                    diff_num,
                    diff_ccy,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Pad):
                pad_rows.append((
                    entry.date.isoformat(),
                    entry.account,
                    entry.source_account,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Price):
                price_rows.append((
                    entry.date.isoformat(),
                    entry.currency,
                    str(entry.amount.number),
                    entry.amount.currency,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Note):
                tags = list(entry.tags) if getattr(entry, 'tags', None) else None
                links = list(entry.links) if getattr(entry, 'links', None) else None
                note_rows.append((
                    entry.date.isoformat(),
                    entry.account,
                    entry.comment,
                    json.dumps(tags) if tags else None,
                    json.dumps(links) if links else None,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Event):
                event_rows.append((
                    entry.date.isoformat(),
                    entry.type,
                    entry.description,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Document):
                tags = list(entry.tags) if getattr(entry, 'tags', None) else None
                links = list(entry.links) if getattr(entry, 'links', None) else None
                document_rows.append((
                    entry.date.isoformat(),
                    entry.account,
                    entry.filename,
                    json.dumps(tags) if tags else None,
                    json.dumps(links) if links else None,
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Custom):
                values = []
                for v in (entry.values or []):
                    values.append(self._convert_value_to_json_serializable(v))
                custom_rows.append((
                    entry.date.isoformat(),
                    entry.type,
                    json.dumps(values),
                    self._metadata_to_json(entry.meta),
                ))

            elif isinstance(entry, data.Query):
                query_rows.append((
                    entry.date.isoformat(),
                    entry.name,
                    entry.query_string,
                    self._metadata_to_json(entry.meta),
                ))

        # ── 2. Bulk insert directive tables ──────────────────────────────
        con.executemany(
            "INSERT INTO accounts (name, open_date, close_date, currencies_json, booking, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            account_rows,
        )
        # Apply close dates
        for close_date, account_name in close_updates:
            con.execute(
                "UPDATE accounts SET close_date = ? WHERE name = ?",
                (close_date, account_name),
            )

        con.executemany(
            "INSERT OR REPLACE INTO commodities (code, declaration_date, name, type, metadata_json) "
            "VALUES (?,?,?,?,?)",
            commodity_rows,
        )
        con.executemany(
            "INSERT INTO balance_assertions "
            "(date, account, amount_number, amount_currency, tolerance, passed, diff_number, diff_currency, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            balance_rows,
        )
        con.executemany(
            "INSERT INTO pad_directives (date, account, source_account, metadata_json) "
            "VALUES (?,?,?,?)",
            pad_rows,
        )
        con.executemany(
            "INSERT INTO prices (date, base_currency, quote_number, quote_currency, metadata_json) "
            "VALUES (?,?,?,?,?)",
            price_rows,
        )
        con.executemany(
            "INSERT INTO notes (date, account, comment, tags_json, links_json, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            note_rows,
        )
        con.executemany(
            "INSERT INTO events (date, type, description, metadata_json) "
            "VALUES (?,?,?,?)",
            event_rows,
        )
        con.executemany(
            "INSERT INTO documents (date, account, filename, tags_json, links_json, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            document_rows,
        )
        con.executemany(
            "INSERT INTO custom_directives (date, type, values_json, metadata_json) "
            "VALUES (?,?,?,?)",
            custom_rows,
        )
        con.executemany(
            "INSERT INTO stored_queries (date, name, query_string, metadata_json) "
            "VALUES (?,?,?,?)",
            query_rows,
        )

        # ── 3. Computed state: account balances + lots ───────────────────
        self._export_account_balances(con, entries)
        self._export_lots(con, entries)

        # ── 4. Commodity usage ───────────────────────────────────────────
        self._export_commodity_usage(con, entries)

        # ── 5. Training data ─────────────────────────────────────────────
        self._export_training_data(con, entries)

        # ── 6. Errors ────────────────────────────────────────────────────
        self._export_errors(con, errors)

        # ── 7. Options ───────────────────────────────────────────────────
        self._export_options(con, options)

        logger.info(
            "Full ledger export: %d accounts, %d commodities, %d balances, "
            "%d prices, %d errors",
            len(account_rows), len(commodity_rows), len(balance_rows),
            len(price_rows), len(errors),
        )

    def _export_account_balances(
        self, con: sqlite3.Connection, entries: List[Any]
    ) -> None:
        """Compute and export per-account, per-currency balances using realization."""
        from beancount.core.realization import realize, iter_children

        real_root = realize(entries)

        # Also build transaction count + last date from entries
        acct_stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for entry in entries:
            if not isinstance(entry, Transaction):
                continue
            for posting in entry.postings:
                if posting.units is None:
                    continue
                ccy = posting.units.currency
                acct = posting.account
                if acct not in acct_stats:
                    acct_stats[acct] = {}
                if ccy not in acct_stats[acct]:
                    acct_stats[acct][ccy] = {"count": 0, "last_date": None}
                acct_stats[acct][ccy]["count"] += 1
                prev = acct_stats[acct][ccy]["last_date"]
                if prev is None or entry.date > prev:
                    acct_stats[acct][ccy]["last_date"] = entry.date

        rows = []
        for real_account in iter_children(real_root):
            account_name = real_account.account
            if not account_name:
                continue
            for position in real_account.balance:
                ccy = position.units.currency
                stats = (acct_stats.get(account_name) or {}).get(ccy, {})
                rows.append((
                    account_name,
                    ccy,
                    str(position.units.number),
                    stats.get("count", 0),
                    stats["last_date"].isoformat() if stats.get("last_date") else None,
                ))

        con.executemany(
            "INSERT INTO account_balances "
            "(account, currency, balance, transaction_count, last_transaction_date) "
            "VALUES (?,?,?,?,?)",
            rows,
        )

    def _export_lots(self, con: sqlite3.Connection, entries: List[Any]) -> None:
        """Extract current lot positions (positions with cost basis) from realized accounts."""
        from beancount.core.realization import realize, iter_children

        real_root = realize(entries)

        rows = []
        for real_account in iter_children(real_root):
            account_name = real_account.account
            if not account_name:
                continue
            for position in real_account.balance:
                if position.cost is not None:
                    book_value = str(position.units.number * position.cost.number)
                    rows.append((
                        account_name,
                        str(position.units.number),
                        position.units.currency,
                        str(position.cost.number),
                        position.cost.currency,
                        position.cost.date.isoformat() if position.cost.date else "",
                        position.cost.label,
                        book_value,
                    ))

        con.executemany(
            "INSERT OR REPLACE INTO lots "
            "(account, units_number, units_currency, cost_number, cost_currency, "
            "acquisition_date, label, book_value) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )

    def _export_commodity_usage(
        self, con: sqlite3.Connection, entries: List[Any]
    ) -> None:
        """Compute and export commodity usage statistics from transactions."""
        usage: Dict[str, Dict[str, Any]] = {}

        for entry in entries:
            if not isinstance(entry, Transaction):
                continue
            for posting in entry.postings:
                if posting.units is None:
                    continue
                code = posting.units.currency
                if code not in usage:
                    usage[code] = {
                        "count": 0,
                        "volume": Decimal(0),
                        "first": entry.date,
                        "last": entry.date,
                    }
                usage[code]["count"] += 1
                if posting.units.number is not None:
                    usage[code]["volume"] += abs(posting.units.number)
                if entry.date < usage[code]["first"]:
                    usage[code]["first"] = entry.date
                if entry.date > usage[code]["last"]:
                    usage[code]["last"] = entry.date

        rows = [
            (code, u["count"], str(u["volume"]), u["first"].isoformat(), u["last"].isoformat())
            for code, u in usage.items()
        ]
        con.executemany(
            "INSERT INTO commodity_usage (code, transaction_count, total_volume, first_seen, last_seen) "
            "VALUES (?,?,?,?,?)",
            rows,
        )

    def _export_training_data(
        self, con: sqlite3.Connection, entries: List[Any]
    ) -> None:
        """Export payee/narration → category pairs for ML categorization."""
        rows = []
        for entry in entries:
            if not isinstance(entry, Transaction):
                continue
            if not entry.payee:
                continue

            memo = ""
            if entry.meta:
                memo = entry.meta.get('memo', '') or entry.meta.get('ofx_memo', '')

            description_parts = [entry.payee]
            if memo:
                description_parts.append(memo)
            if entry.narration:
                description_parts.append(entry.narration)
            description = " ".join(description_parts).strip()

            for posting in entry.postings:
                if posting.account.startswith(INCOME_STATEMENT_PREFIXES):
                    rows.append((description, posting.account))
                    break

        con.executemany(
            "INSERT INTO training_data (description, category) VALUES (?,?)",
            rows,
        )

    def _export_errors(
        self, con: sqlite3.Connection, errors: List[Any]
    ) -> None:
        """Export Beancount parsing errors."""
        rows = []
        for err in errors:
            source_file = None
            line_number = None
            if hasattr(err, 'source') and err.source:
                source_file = err.source.get('filename')
                line_number = err.source.get('lineno')
            entry_json = None
            if hasattr(err, 'entry') and err.entry:
                try:
                    entry_json = json.dumps(
                        self._convert_value_to_json_serializable(err.entry)
                    )
                except Exception:
                    pass
            rows.append((
                source_file,
                line_number,
                err.message if hasattr(err, 'message') else str(err),
                entry_json,
            ))
        con.executemany(
            "INSERT INTO ledger_errors (source_file, line_number, message, entry_json) "
            "VALUES (?,?,?,?)",
            rows,
        )

    def _export_options(
        self, con: sqlite3.Connection, options: Dict[str, Any]
    ) -> None:
        """Export Beancount options (skipping non-serializable values)."""
        rows = []
        for key, value in options.items():
            try:
                value_json = json.dumps(
                    self._convert_value_to_json_serializable(value)
                )
                rows.append((key, value_json))
            except (TypeError, ValueError):
                # Skip non-serializable values (e.g. DisplayContext)
                continue
        con.executemany(
            "INSERT INTO ledger_options (key, value_json) VALUES (?,?)",
            rows,
        )

    # ── Top-level blocking export methods ────────────────────────────────────

    def _export_transactions_to_sqlite(
        self,
        transactions: List[Transaction]
    ) -> Dict[str, Any]:
        """
        Export transactions to SQLite — postings table only (legacy path).
        Runs in a thread pool to avoid blocking the event loop.
        """
        con = self._open_connection()
        self._ensure_postings_table(con)

        try:
            postings_count = self._export_postings(con, transactions)
            con.commit()
            logger.info(
                f"Successfully exported {postings_count} postings "
                f"from {len(transactions)} transactions"
            )
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        return {
            "postings_count": postings_count,
            "transactions_count": len(transactions),
        }

    def _export_full_to_sqlite(
        self,
        entries: List[Any],
        errors: List[Any],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Full export — postings + all ledger-mirror tables in one atomic txn.
        Runs in a thread pool.
        """
        transactions = [e for e in entries if isinstance(e, Transaction)]

        con = self._open_connection()
        self._ensure_postings_table(con)
        self._ensure_new_tables(con)

        try:
            # Existing postings export (unchanged logic)
            postings_count = self._export_postings(con, transactions)

            # New: full ledger export (same atomic transaction)
            self._export_full_ledger(con, entries, errors, options)

            con.commit()
            logger.info(
                "Full SQLite export committed: %d postings from %d transactions",
                postings_count, len(transactions),
            )
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        return {
            "postings_count": postings_count,
            "transactions_count": len(transactions),
        }

    # ── Static helper methods (unchanged) ────────────────────────────────────

    @staticmethod
    def _get_account_type(account: str) -> str:
        """Extract account type from account name (first component before colon)."""
        return account.split(':')[0] if ':' in account else account

    @staticmethod
    def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
        """Convert Decimal to float for database storage."""
        return float(value) if value is not None else None

    @staticmethod
    def _serialize_array(arr: List[str]) -> str:
        """Convert Python list to JSON string for SQLite storage."""
        return json.dumps(arr) if arr else '[]'

    @staticmethod
    def _compute_source_account(
        txn: Transaction,
        current_posting: Posting
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Compute the source account for a posting based on the rules:
        1. If 'source_account' metadata exists on transaction, use that
        2. If only 2 postings, use the other posting's account
        3. If multiple postings but only one is Assets/Liabilities, use that
        4. Otherwise, pick the first Assets/Liabilities account (excluding current posting)
        """
        # Rule 1: Check for explicit source_account metadata
        if txn.meta and 'source_account' in txn.meta:
            source_acc = txn.meta['source_account']
            return source_acc, SQLiteExporter._get_account_type(source_acc)

        # Get all postings except the current one
        other_postings = [p for p in txn.postings if p is not current_posting]

        # Rule 2: If only 2 postings total, use the other one
        if len(txn.postings) == 2:
            source_acc = other_postings[0].account
            return source_acc, SQLiteExporter._get_account_type(source_acc)

        # Rule 3 & 4: Find Assets/Liabilities accounts
        asset_liability_postings = [
            p for p in other_postings
            if SQLiteExporter._get_account_type(p.account) in SOURCE_ACCOUNT_TYPES
        ]

        if len(asset_liability_postings) == 1:
            source_acc = asset_liability_postings[0].account
            return source_acc, SQLiteExporter._get_account_type(source_acc)
        elif len(asset_liability_postings) > 1:
            # Pick the first one
            source_acc = asset_liability_postings[0].account
            return source_acc, SQLiteExporter._get_account_type(source_acc)

        # No clear source account found
        return None, None

    @staticmethod
    def _extract_tags(txn: Transaction) -> List[str]:
        """Extract tags from transaction as a list."""
        return list(txn.tags) if txn.tags else []

    @staticmethod
    def _extract_links(txn: Transaction) -> List[str]:
        """Extract links from transaction as a list."""
        return list(txn.links) if txn.links else []

    @staticmethod
    def _convert_value_to_json_serializable(value: Any) -> Any:
        """Recursively convert values to JSON-serializable types."""
        if isinstance(value, Decimal):
            return float(value)
        elif isinstance(value, (date, datetime)):
            return value.isoformat()
        elif isinstance(value, (str, int, float, bool, type(None))):
            return value
        elif isinstance(value, dict):
            return {k: SQLiteExporter._convert_value_to_json_serializable(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple, set, frozenset)):
            return [SQLiteExporter._convert_value_to_json_serializable(item) for item in value]
        else:
            # Convert other types to string
            return str(value)

    @staticmethod
    def _metadata_to_json(meta: Optional[Dict[str, Any]]) -> Optional[str]:
        """Convert metadata dictionary to JSON string, handling special types."""
        if not meta:
            return None

        # Recursively convert all values to JSON-serializable types
        clean_meta = {
            k: SQLiteExporter._convert_value_to_json_serializable(v)
            for k, v in meta.items()
        }

        return json.dumps(clean_meta)

    @staticmethod
    def _get_transaction_id(txn: Transaction, txn_index: int) -> str:
        """
        Get transaction ID from metadata.

        Priority:
        1. Use 'id' field (new UUIDv7 system)
        2. Fallback to 'transaction_id' (old system, for migration)
        3. Generate from date-index (legacy fallback)
        """
        if txn.meta:
            # New system
            if 'id' in txn.meta:
                return str(txn.meta['id'])

            # Old system (migration support)
            if 'transaction_id' in txn.meta:
                return str(txn.meta['transaction_id'])

        # Legacy fallback
        return f"{txn.date.isoformat()}-{txn_index}"

    @staticmethod
    def _get_content_hash(txn: Transaction) -> Optional[str]:
        """
        Get content hash from metadata.

        Returns None if not present (will be computed on demand).
        """
        if txn.meta and 'content_hash' in txn.meta:
            return str(txn.meta['content_hash'])
        return None
