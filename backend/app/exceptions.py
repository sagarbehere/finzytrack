"""
Simple API exceptions for Finzytrack.
"""

from typing import Dict, Any, Optional

from app import error_codes as ec


class APIError(Exception):
    """
    Simple API error with message, HTTP status, and optional details.
    
    This single exception class handles all application errors with just
    the essential information needed for frontend error handling.
    """
    
    def __init__(
        self, 
        message: str, 
        code: str,
        status_code: int = 400, 
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


def convert_stdlib_exception(exc: Exception) -> APIError:
    """Convert standard Python exceptions to APIError.

    KeyError is intentionally NOT mapped to VALIDATION_ERROR — a KeyError
    inside service code is almost always a bug (a dict lookup expected a key
    that wasn't there), not a user-input problem. Falling through to
    UNKNOWN_SERVER_ERROR / 500 gives the user an honest 'server bug' rather
    than a misleading 400 that implies they sent bad input.
    """
    if isinstance(exc, APIError):
        return exc

    if isinstance(exc, FileNotFoundError):
        return APIError(f"File not found: {exc.filename}", code=ec.FILE_NOT_FOUND, status_code=404, details={"path": str(exc.filename)})
    elif isinstance(exc, PermissionError):
        return APIError("Permission denied", code=ec.FILE_PERMISSION_ERROR, status_code=403, details={"error": str(exc)})
    elif isinstance(exc, ValueError):
        return APIError(f"Invalid value: {str(exc)}", code=ec.VALIDATION_ERROR, status_code=400)
    else:
        return APIError("Internal server error", code=ec.UNKNOWN_SERVER_ERROR, status_code=500, details={"error": str(exc)})