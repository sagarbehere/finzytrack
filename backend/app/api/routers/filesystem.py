"""
Filesystem Browse API Router - Provides directory listing for the file picker UI.

Endpoints:
- GET /api/filesystem/browse  — list entries in a directory
"""

from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request

from app.app_mode import AppMode, UserContext
from app.exceptions import APIError
from app.helpers.path_guard import guard_path
from app.helpers.response_helpers import success_json_response
from app.middleware.auth import get_user_context
from app.schemas.filesystem_schemas import FileEntry, BrowseResponse
from app.schemas.response_schemas import ApiResponse
from app import error_codes as ec

router = APIRouter()


# ============================================================================
# Security helpers
# ============================================================================

def _resolve_safe(raw_path: str) -> Path:
    """
    Resolve a path string to an absolute, symlink-resolved Path.

    Raises APIError if:
    - The path does not exist
    - The path is not a directory
    """
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise APIError(
            f"Directory not found: {raw_path}",
            code=ec.DIRECTORY_NOT_FOUND,
            status_code=404,
        )
    except PermissionError:
        raise APIError(
            f"Permission denied: {raw_path}",
            code=ec.PERMISSION_DENIED,
            status_code=403,
        )
    except (OSError, RuntimeError) as e:
        raise APIError(
            f"Cannot access path: {e}",
            code=ec.PATH_ACCESS_ERROR,
            status_code=400,
        )

    if not resolved.is_dir():
        raise APIError(
            f"Not a directory: {raw_path}",
            code=ec.NOT_A_DIRECTORY,
            status_code=400,
        )

    return resolved


# ============================================================================
# Endpoint
# ============================================================================

@router.get("/filesystem/browse", response_model=ApiResponse[BrowseResponse])
async def browse_directory(
    request: Request,
    path: Optional[str] = Query(
        default=None,
        description="Directory to list. Defaults to current working directory.",
    ),
    mode: Literal["file", "directory"] = Query(
        default="file",
        description="Selection mode: 'file' shows files and directories, 'directory' shows directories only.",
    ),
    extensions: Optional[str] = Query(
        default=None,
        description="Comma-separated file extensions to filter by (e.g. '.beancount,.bean'). "
                    "Only applies when mode=file. Directories are always shown.",
    ),
    ctx: UserContext = Depends(get_user_context),
):
    """
    List entries in a directory for the file picker UI.

    Returns directories first (sorted), then files (sorted).
    Hides dotfiles/dotdirs. Applies extension filtering when requested.
    """
    # Determine starting directory
    if path:
        target = _resolve_safe(path)
    else:
        target = Path.cwd().resolve()

    # In hosted mode, restrict browsing to the user's own directory tree.
    # Desktop mode is unrestricted (file picker needs full filesystem access).
    if request.app.state.mode == AppMode.HOSTED:
        guard_path(target, ctx.root_dir, "browse path")

    home = Path.home().resolve()

    # Parse extension filter
    ext_filter: Optional[set[str]] = None
    if extensions and mode == "file":
        ext_filter = {
            ext.strip().lower() if ext.strip().startswith(".") else f".{ext.strip().lower()}"
            for ext in extensions.split(",")
            if ext.strip()
        }

    # List directory entries
    entries: List[FileEntry] = []
    try:
        for item in target.iterdir():
            # Skip dotfiles/dotdirs
            if item.name.startswith("."):
                continue

            try:
                if item.is_dir():
                    entries.append(FileEntry(name=item.name, type="directory"))
                elif mode == "file" and item.is_file():
                    # Apply extension filter
                    if ext_filter and item.suffix.lower() not in ext_filter:
                        continue
                    try:
                        size = item.stat().st_size
                    except OSError:
                        size = None
                    entries.append(FileEntry(name=item.name, type="file", size=size))
            except (PermissionError, OSError):
                # Skip entries we can't stat (broken symlinks, permission issues)
                continue
    except PermissionError:
        raise APIError(
            f"Permission denied reading directory: {target}",
            code=ec.PERMISSION_DENIED,
            status_code=403,
        )

    # Sort: directories first (alphabetical), then files (alphabetical)
    entries.sort(key=lambda e: (0 if e.type == "directory" else 1, e.name.lower()))

    # Compute parent path (None at filesystem root)
    parent = target.parent
    parent_path = str(parent) if parent != target else None

    return success_json_response(BrowseResponse(
        current_path=str(target),
        parent_path=parent_path,
        home_path=str(home),
        entries=entries,
    ))
