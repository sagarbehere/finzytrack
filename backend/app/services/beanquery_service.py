"""
Beanquery Service - Executes Beancount queries using beanquery.

This service handles the execution of native Beancount query language queries
against the cached Beancount entries.
"""
import time
import asyncio
import logging
from typing import List, Dict, Any, Tuple
from decimal import Decimal

from beancount.core.inventory import Inventory
from beancount.core.amount import Amount
from beancount.core.position import Position
from beanquery import query

logger = logging.getLogger(__name__)


class BeanqueryService:
    """Service for executing Beanquery queries."""

    def __init__(self):
        """Initialize the beanquery service."""
        pass

    async def execute_query(
        self,
        entries: List[Any],
        options: Any,
        query_str: str
    ) -> Dict[str, Any]:
        """
        Execute a beanquery against Beancount entries.

        Args:
            entries: Parsed Beancount entries from cache
            options: Beancount options
            query_str: Beanquery string to execute

        Returns:
            Dictionary with query results and metadata
        """
        start_time = time.time()

        try:
            # Run blocking beanquery operations in thread pool
            result_types, result_rows = await asyncio.to_thread(
                self._execute_beanquery,
                entries,
                options,
                query_str
            )

            # Get column names from result_types
            columns = []
            for col in result_types:
                column_name = col[0]
                # Determine column type from the first row if available
                col_type = "TEXT"  # Default type
                if result_rows and len(result_rows) > 0:
                    first_value = result_rows[0][result_types.index(col)]
                    col_type = self._determine_column_type(first_value)
                
                columns.append({
                    "name": column_name,
                    "type": col_type
                })

            # Convert rows to list of dictionaries
            rows = []
            for row in result_rows:
                record = {}
                for i, value in enumerate(row):
                    column_name = result_types[i][0] if i < len(result_types) else f"col_{i}"
                    record[column_name] = self._serialize_value(value)
                rows.append(record)

            execution_time_ms = int((time.time() - start_time) * 1000)

            return {
                "success": True,
                "execution_time_ms": execution_time_ms,
                "row_count": len(rows),
                "columns": columns,
                "rows": rows
            }

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Beanquery execution failed: {e}", exc_info=True)
            raise Exception(f"Failed to execute beanquery: {str(e)}")

    def _execute_beanquery(
        self,
        entries: List[Any],
        options: Any,
        query_str: str
    ) -> Tuple[List[Tuple], List[Tuple]]:
        """
        Execute beanquery (blocking I/O).

        This method runs in a thread pool to avoid blocking the event loop.

        Args:
            entries: Beancount entries
            options: Beancount options
            query_str: Query string

        Returns:
            Tuple of (result_types, result_rows)
        """
        result_types, result_rows = query.run_query(entries, options, query_str)
        
        # Convert to the expected type format
        # result_types can be a list of Column objects, tuples, or other structures
        formatted_types: List[Tuple] = []
        if result_types:
            for col in result_types:
                if isinstance(col, tuple):
                    # Already a tuple, use as-is
                    formatted_types.append(col)
                elif hasattr(col, 'name'):  # Column-like object
                    # Extract name and try to get type if available
                    col_name = getattr(col, 'name', str(col))
                    col_type = getattr(col, 'type', 'TEXT')
                    formatted_types.append((col_name, col_type))
                else:
                    # Fallback: convert to string with default type
                    formatted_types.append((str(col), 'TEXT'))
        
        return formatted_types, result_rows

    def _serialize_value(self, value: Any) -> Any:
        """Convert beancount values to JSON-serializable format."""
        if value is None:
            return None
        elif isinstance(value, Decimal):
            return float(value)
        elif isinstance(value, Inventory):
            if value.is_empty():
                return 0
            # Sum all positions in the inventory (handles multiple currencies)
            total = 0.0
            for pos in value:
                if hasattr(pos, 'units') and pos.units and pos.units.number is not None:
                    total += float(pos.units.number)
            return total
        elif isinstance(value, Amount):
            return float(value.number) if value.number is not None else 0
        elif isinstance(value, Position):
            return float(value.units.number) if hasattr(value, 'units') and value.units and value.units.number is not None else 0
        elif hasattr(value, '_asdict'):  # Named tuples
            return str(value)
        elif hasattr(value, '__dict__'):  # Other objects with attributes
            return str(value)
        else:
            return value

    def _determine_column_type(self, value: Any) -> str:
        """Determine the column type from a sample value."""
        if value is None:
            return "TEXT"
        elif isinstance(value, (int, float, Decimal)):
            return "REAL"
        elif isinstance(value, str):
            return "TEXT"
        elif isinstance(value, bool):
            return "BOOLEAN"
        elif isinstance(value, (Inventory, Amount, Position)):
            return "REAL"  # These represent monetary values
        else:
            return "TEXT"  # Default for complex types