"""
Auth dependency — resolves the current ``UserContext`` from each request.

Desktop mode:  always returns a fixed ``"local"`` user (no auth).
Hosted mode:   reads ``X-User-ID`` header injected by finzytrack-cloud
               (the external auth proxy).  No JWT validation happens
               here — that is finzytrack-cloud's responsibility.
"""

import re
import logging

from fastapi import Request

from app.app_mode import AppMode, UserContext
from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


async def get_user_context(request: Request) -> UserContext:
    """FastAPI dependency that resolves the authenticated user context."""
    mode: AppMode = request.app.state.mode

    if mode == AppMode.DESKTOP:
        return UserContext(
            user_id="local",
            root_dir=request.app.state.root_dir,
        )

    # Hosted mode — expect X-User-ID from the auth proxy
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise APIError(
            message="Authentication required",
            code=ec.AUTH_REQUIRED,
            status_code=401,
        )

    if not _USER_ID_RE.match(user_id):
        raise APIError(
            message="Invalid user ID format",
            code=ec.INVALID_USER_ID,
            status_code=400,
        )

    return UserContext(
        user_id=user_id,
        root_dir=request.app.state.base_dir / "users" / user_id,
    )
