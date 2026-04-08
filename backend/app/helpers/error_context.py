"""
Reusable error-handling context managers to eliminate duplicated try/except
blocks across route handlers.
"""

from contextlib import contextmanager
from typing import Optional

from app.exceptions import APIError


@contextmanager
def ledger_error_context(ledger_path: Optional[str] = None):
    """
    Context manager that catches common ledger-access exceptions and
    converts them to APIError. Re-raises existing APIErrors unchanged.

    Usage::

        with ledger_error_context(config.ledger_file):
            result = beancount_manager.some_operation()
    """
    try:
        yield
    except APIError:
        raise
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found",
            code="FILE_NOT_FOUND",
            status_code=404,
            details={"path": ledger_path} if ledger_path else {},
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file",
            code="FILE_PERMISSION_ERROR",
            status_code=403,
            details={"path": ledger_path} if ledger_path else {},
        )
    except Exception as e:
        raise APIError(
            message=f"Error accessing ledger: {str(e)}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500,
        )
