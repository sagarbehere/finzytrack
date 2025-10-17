"""
DuckDB Exporter Service - Converts Beancount entries to DuckDB format.

This service is responsible for the actual data conversion (the worker).
It accepts parsed Beancount entries and exports them to a DuckDB database.
"""
import os
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime

import duckdb
from beancount.core.data import Transaction, Posting

logger = logging.getLogger(__name__)


class DuckDBExporter:
    """Service for exporting Beancount entries to DuckDB format."""

    def __init__(self, duckdb_path: str):
        """
        Initialize DuckDB exporter.

        Args:
            duckdb_path: Path to the DuckDB database file
        """
        self.duckdb_path = duckdb_path
        self.export_path = duckdb_path  # Generic interface

    async def export_entries(
        self,
        entries: List[Any],
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Export Beancount entries to DuckDB.

        Args:
            entries: Parsed Beancount entries from cache
            force: Regenerate even if up-to-date

        Returns:
            Dictionary with:
                - success: bool
                - postings_count: int
                - transactions_count: int
                - duration_ms: int
                - path: str
                - last_sync: str (ISO timestamp)
        """
        start_time = time.time()

        try:
            # Filter for transactions only
            transactions = [e for e in entries if isinstance(e, Transaction)]
            logger.info(f"Exporting {len(transactions)} transactions to DuckDB")

            # Run blocking DuckDB operations in thread pool
            result = await asyncio.to_thread(
                self._export_transactions_to_duckdb,
                transactions
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": True,
                "postings_count": result["postings_count"],
                "transactions_count": result["transactions_count"],
                "duration_ms": duration_ms,
                "path": str(self.duckdb_path),
                "last_sync": datetime.utcnow().isoformat() + "Z"
            }

        except Exception as e:
            logger.error(f"DuckDB export failed: {e}", exc_info=True)
            raise Exception(f"Failed to export to DuckDB: {str(e)}")

    async def get_status(self) -> Dict[str, Any]:
        """
        Get DuckDB export status.

        Returns:
            Dictionary with:
                - exists: bool
                - path: str
                - size_bytes: int
                - last_modified: str (ISO timestamp)
                - postings_count: int (if exists)
        """
        try:
            db_path = Path(self.duckdb_path)

            if not db_path.exists():
                return {
                    "exists": False,
                    "path": str(self.duckdb_path),
                    "size_bytes": 0,
                    "last_modified": None,
                    "postings_count": 0
                }

            # Get file stats
            stat = db_path.stat()
            last_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

            # Get postings count from database
            postings_count = await asyncio.to_thread(
                self._get_postings_count
            )

            return {
                "exists": True,
                "path": str(self.duckdb_path),
                "size_bytes": stat.st_size,
                "last_modified": last_modified,
                "postings_count": postings_count
            }

        except Exception as e:
            logger.error(f"Failed to get DuckDB status: {e}", exc_info=True)
            return {
                "exists": False,
                "path": str(self.duckdb_path),
                "size_bytes": 0,
                "last_modified": None,
                "postings_count": 0,
                "error": str(e)
            }

    def _get_postings_count(self) -> int:
        """Get count of postings in the database (blocking I/O)."""
        try:
            con = duckdb.connect(self.duckdb_path, read_only=True)
            result = con.execute("SELECT COUNT(*) FROM postings").fetchone()
            con.close()
            return result[0] if result else 0
        except Exception as e:
            logger.warning(f"Could not get postings count: {e}")
            return 0

    def _export_transactions_to_duckdb(
        self,
        transactions: List[Transaction]
    ) -> Dict[str, Any]:
        """
        Export transactions to DuckDB (blocking I/O).

        This method runs in a thread pool to avoid blocking the event loop.

        Args:
            transactions: List of Beancount Transaction objects

        Returns:
            Dictionary with postings_count and transactions_count
        """
        # Ensure parent directory exists
        db_path = Path(self.duckdb_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create DuckDB connection
        con = duckdb.connect(self.duckdb_path)

        # Check if table exists
        cursor = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'postings'"
        )
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            # First time: create table
            schema_sql = """
            CREATE TABLE postings (
                posting_id INTEGER PRIMARY KEY,
                transaction_id VARCHAR,
                transaction_content_hash VARCHAR,
                transaction_date DATE,
                transaction_flag VARCHAR,
                transaction_payee VARCHAR,
                transaction_narration VARCHAR,
                transaction_tags VARCHAR[],
                transaction_links VARCHAR[],
                account VARCHAR,
                account_type VARCHAR,
                amount DOUBLE,
                currency VARCHAR,
                cost_amount DOUBLE,
                cost_currency VARCHAR,
                price_amount DOUBLE,
                price_currency VARCHAR,
                source_account VARCHAR,
                source_account_type VARCHAR,
                transaction_metadata_json JSON,
                posting_metadata_json JSON,
                year INTEGER,
                month INTEGER,
                quarter INTEGER,
                year_month VARCHAR
            );

            CREATE INDEX idx_transaction_id ON postings(transaction_id);
            CREATE INDEX idx_content_hash ON postings(transaction_content_hash);
            """
            con.execute(schema_sql)
            logger.info("Created postings table")
        else:
            # Table exists: delete all rows (preserves schema)
            con.execute("DELETE FROM postings")
            logger.debug("Cleared existing postings data")

        # Prepare data for bulk insert
        posting_id = 0
        rows = []

        for txn_index, txn in enumerate(transactions):
            transaction_id = self._get_transaction_id(txn, txn_index)
            transaction_content_hash = self._get_content_hash(txn)
            transaction_tags = self._extract_tags(txn)
            transaction_links = self._extract_links(txn)
            transaction_metadata = self._metadata_to_json(txn.meta)

            for posting in txn.postings:
                posting_id += 1

                # Compute source account
                source_account, source_account_type = self._compute_source_account(txn, posting)

                # Extract posting metadata
                posting_metadata = self._metadata_to_json(posting.meta) if posting.meta else None

                # Extract cost information
                cost_amount = None
                cost_currency = None
                if posting.cost:
                    # Use getattr for type-safe access to Cost/CostSpec attributes
                    cost_number = getattr(posting.cost, 'number', None)
                    if cost_number is not None:
                        cost_amount = self._decimal_to_float(cost_number)
                    cost_currency = getattr(posting.cost, 'currency', None)

                # Extract price information
                price_amount = None
                price_currency = None
                if posting.price:
                    price_amount = self._decimal_to_float(posting.price.number)
                    price_currency = posting.price.currency

                # Time-based fields
                year = txn.date.year
                month = txn.date.month
                quarter = (month - 1) // 3 + 1
                year_month = f"{year}-{month:02d}"

                row = (
                    posting_id,
                    transaction_id,
                    transaction_content_hash,
                    txn.date,
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
                    year_month
                )
                rows.append(row)

        # Bulk insert
        con.executemany("""
            INSERT INTO postings VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, rows)

        # Verify
        result = con.execute("SELECT COUNT(*) FROM postings").fetchone()
        postings_count = result[0] if result else 0

        con.close()

        logger.info(f"Successfully exported {postings_count} postings from {len(transactions)} transactions")

        return {
            "postings_count": postings_count,
            "transactions_count": len(transactions)
        }

    # Helper methods (adapted from reference script)

    @staticmethod
    def _get_account_type(account: str) -> str:
        """Extract account type from account name (first component before colon)."""
        return account.split(':')[0] if ':' in account else account

    @staticmethod
    def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
        """Convert Decimal to float for database storage."""
        return float(value) if value is not None else None

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
            return source_acc, DuckDBExporter._get_account_type(source_acc)

        # Get all postings except the current one
        other_postings = [p for p in txn.postings if p is not current_posting]

        # Rule 2: If only 2 postings total, use the other one
        if len(txn.postings) == 2:
            source_acc = other_postings[0].account
            return source_acc, DuckDBExporter._get_account_type(source_acc)

        # Rule 3 & 4: Find Assets/Liabilities accounts
        asset_liability_postings = [
            p for p in other_postings
            if DuckDBExporter._get_account_type(p.account) in ('Assets', 'Liabilities')
        ]

        if len(asset_liability_postings) == 1:
            source_acc = asset_liability_postings[0].account
            return source_acc, DuckDBExporter._get_account_type(source_acc)
        elif len(asset_liability_postings) > 1:
            # Pick the first one
            source_acc = asset_liability_postings[0].account
            return source_acc, DuckDBExporter._get_account_type(source_acc)

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
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, (str, int, float, bool, type(None))):
            return value
        elif isinstance(value, dict):
            return {k: DuckDBExporter._convert_value_to_json_serializable(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple, set)):
            return [DuckDBExporter._convert_value_to_json_serializable(item) for item in value]
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
            k: DuckDBExporter._convert_value_to_json_serializable(v)
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
