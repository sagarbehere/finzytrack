"""
Generalised path-traversal guard.

Use ``guard_path`` wherever user-supplied path components are resolved
against a trusted base directory to ensure the result stays within the
allowed directory tree.
"""

from pathlib import Path

from app.exceptions import APIError
from app import error_codes as ec


def guard_path(resolved_path: Path, jail: Path, context: str = "path") -> Path:
    """Ensure *resolved_path* is inside *jail*.

    Both paths are fully resolved (symlinks followed) before comparison.
    Raises ``APIError`` with 403 if the path escapes the jail.

    Returns the resolved path on success.
    """
    jail_resolved = jail.resolve()
    path_resolved = resolved_path.resolve()
    if not path_resolved.is_relative_to(jail_resolved):
        raise APIError(
            message=f"Access denied: {context} escapes allowed directory",
            code=ec.INVALID_PATH,
            status_code=403,
        )
    return path_resolved
