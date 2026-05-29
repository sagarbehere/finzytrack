"""
Per-request user_id correlation for log records.

Substrate, not a feature: every existing ``logger.info(...)`` / ``warning(...)``
call site picks up a ``user_id`` field automatically once this module is wired
into the log handlers (see ``main.py:setup_logging``). No call site needs to
change; ``get_user_context`` (FastAPI dependency) sets the contextvar at the
start of each request and asyncio scopes the value per task, so concurrent
requests don't see each other's user_id.

When the F1 structured-logging effort lands, swap the formatter for a JSON
formatter — the filter and contextvar plumbing stay the same.
"""

import contextvars
import logging
from typing import Optional

# Per-task user_id. None when no request is in scope (startup logs, shutdown,
# background work). The placeholder string surfaces as ``user_id=-`` in the
# default text formatter.
_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "finzytrack_user_id", default=None
)

_PLACEHOLDER = "-"


def set_user_id(user_id: Optional[str]) -> None:
    """Attach ``user_id`` to the current async task's context."""
    _user_id_var.set(user_id)


def get_user_id() -> Optional[str]:
    """Return the user_id attached to the current task, if any."""
    return _user_id_var.get()


class UserIdLogFilter(logging.Filter):
    """Injects ``user_id`` onto every ``LogRecord`` from the contextvar.

    With this filter attached to every handler, the log formatter can reference
    ``%(user_id)s`` and get either the active request's user_id or the
    placeholder ``-`` when no request is in flight.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.user_id = _user_id_var.get() or _PLACEHOLDER
        return True
