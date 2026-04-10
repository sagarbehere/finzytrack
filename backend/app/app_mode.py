"""
Application mode and per-request user context.

AppMode determines whether the backend runs as a single-user desktop app
or a multi-user hosted service.  UserContext carries the authenticated
user's identity and root directory for the duration of a request.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AppMode(str, Enum):
    DESKTOP = "desktop"  # Single user, no auth, root_dir = "."
    HOSTED = "hosted"  # Multi-user, X-User-ID header, per-user dirs


@dataclass(frozen=True)
class UserContext:
    """Immutable per-request user identity resolved by the auth layer."""

    user_id: str
    root_dir: Path
