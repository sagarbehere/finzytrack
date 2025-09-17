"""
Simple API exceptions for Finzytrack.
"""

from typing import Dict, Any, Optional


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
    """Convert standard Python exceptions to APIError."""
    if isinstance(exc, APIError):
        return exc
    
    # Map common exceptions to appropriate HTTP status codes and generic codes
    if isinstance(exc, FileNotFoundError):
        return APIError(f"File not found: {exc.filename}", code="FILE_NOT_FOUND", status_code=404, details={"path": str(exc.filename)})
    elif isinstance(exc, PermissionError):
        return APIError("Permission denied", code="FILE_PERMISSION_ERROR", status_code=403, details={"error": str(exc)})
    elif isinstance(exc, ValueError):
        return APIError(f"Invalid value: {str(exc)}", code="VALIDATION_ERROR", status_code=400)
    elif isinstance(exc, KeyError):
        return APIError(f"Missing required field: {str(exc)}", code="VALIDATION_ERROR", status_code=400)
    else:
        return APIError("Internal server error", code="UNKNOWN_SERVER_ERROR", status_code=500, details={"error": str(exc)})