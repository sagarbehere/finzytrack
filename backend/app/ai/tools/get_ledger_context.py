"""
get_ledger_context tool — returns high-level ledger context for analyst mode.

Called once at the start of an analyst conversation to give the LLM awareness
of the user's date range, account hierarchy, and default currency without
requiring a full table scan.
"""

import logging
import sqlite3

from app.ai.tools.base import BaseTool
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)


class GetLedgerContextTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_ledger_context"

    @property
    def description(self) -> str:
        return (
            "Return high-level context about the user's financial ledger: "
            "date range of transactions, account tree with current balances, "
            "and default currency. Call this once at the start of an analyst "
            "conversation to orient yourself before running queries."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def __init__(self, sqlite_reader: SqliteReader, sqlite_path: str):
        self._reader = sqlite_reader
        self._sqlite_path = sqlite_path

    async def execute(self) -> dict:
        try:
            context = self._build_context()
            return {"success": True, **context}
        except Exception as e:
            logger.error(f"get_ledger_context failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _build_context(self) -> dict:
        # Date range and currency from SQLite
        date_range = {"min_date": None, "max_date": None}
        default_currency = None
        try:
            con = sqlite3.connect(self._sqlite_path, uri=True)
            con.execute("PRAGMA query_only = true")
            try:
                row = con.execute(
                    "SELECT MIN(transaction_date), MAX(transaction_date) FROM postings"
                ).fetchone()
                if row:
                    date_range["min_date"] = row[0]
                    date_range["max_date"] = row[1]

                # Most common currency = default
                cur_row = con.execute(
                    "SELECT currency, COUNT(*) as cnt FROM postings "
                    "GROUP BY currency ORDER BY cnt DESC LIMIT 1"
                ).fetchone()
                if cur_row:
                    default_currency = cur_row[0]
            finally:
                con.close()
        except Exception as e:
            logger.warning(f"SQLite context query failed: {e}")

        # Account tree with balances from SQLite
        accounts_with_balances = []
        try:
            con = sqlite3.connect(self._sqlite_path, uri=True)
            con.execute("PRAGMA query_only = true")
            try:
                rows = con.execute(
                    "SELECT account, currency, SUM(CAST(amount AS REAL)) as balance "
                    "FROM postings "
                    "GROUP BY account, currency "
                    "ORDER BY account, currency"
                ).fetchall()
                for row in rows:
                    accounts_with_balances.append({
                        "account": row[0],
                        "currency": row[1],
                        "balance": round(row[2], 2) if row[2] is not None else 0,
                    })
            finally:
                con.close()
        except Exception as e:
            logger.warning(f"SQLite balance query failed: {e}")
            # Fallback: account names from SqliteReader
            for name in sorted(self._reader.get_account_names()):
                accounts_with_balances.append({
                    "account": name,
                    "currency": None,
                    "balance": None,
                })

        return {
            "date_range": date_range,
            "default_currency": default_currency,
            "accounts": accounts_with_balances,
        }
