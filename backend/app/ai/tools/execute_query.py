"""
execute_query tool — runs a read-only SELECT query against the SQLite export
and returns rows as JSON.

Used by the AI assistant to:
- Test SQL queries before embedding them in recipe JSON
- Answer financial questions by querying live data
"""

import logging
import re
import sqlite3
import time

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Allow only SELECT (and WITH ... SELECT for CTEs)
_SELECT_RE = re.compile(r"^\s*(SELECT|WITH\s)", re.IGNORECASE)

# Max rows to return to prevent massive payloads
_MAX_ROWS = 500


class ExecuteQueryTool(BaseTool):
    @property
    def name(self) -> str:
        return "execute_query"

    @property
    def description(self) -> str:
        return (
            "Execute a read-only SQL SELECT query against the user's financial database "
            "(SQLite postings table) and return the result rows as JSON. "
            "Use this to test queries, verify data shape, or answer questions about the "
            "user's finances. Only SELECT statements are allowed."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A SQLite SELECT query to execute against the postings table.",
                },
            },
            "required": ["query"],
        }

    def __init__(self, sqlite_path: str):
        self._sqlite_path = sqlite_path

    async def execute(self, query: str) -> dict:
        # Validate: SELECT-only
        if not _SELECT_RE.match(query):
            return {
                "success": False,
                "error": "Only SELECT queries are allowed. Do not use INSERT, UPDATE, DELETE, DROP, or other statements.",
            }

        try:
            result = self._run_query(query)
            return {
                "success": True,
                "row_count": result["row_count"],
                "columns": result["columns"],
                "rows": result["rows"],
                "execution_time_ms": result["execution_time_ms"],
                "truncated": result["truncated"],
            }
        except sqlite3.OperationalError as e:
            return {"success": False, "error": f"SQL error: {e}"}
        except Exception as e:
            logger.error(f"execute_query failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _run_query(self, query_str: str) -> dict:
        start = time.time()
        con = sqlite3.connect(self._sqlite_path, uri=True)
        con.execute("PRAGMA query_only = true")
        con.row_factory = sqlite3.Row
        try:
            cursor = con.execute(query_str)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows_raw = cursor.fetchmany(_MAX_ROWS + 1)
            truncated = len(rows_raw) > _MAX_ROWS
            if truncated:
                rows_raw = rows_raw[:_MAX_ROWS]

            rows = [{col: row[col] for col in columns} for row in rows_raw]

            return {
                "execution_time_ms": int((time.time() - start) * 1000),
                "row_count": len(rows),
                "columns": columns,
                "rows": rows,
                "truncated": truncated,
            }
        finally:
            con.close()
