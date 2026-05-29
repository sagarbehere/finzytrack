"""
Memory-guarded ledger loader.

This is the ONLY way to call ``loader.load_file()`` in the codebase.
Checks available memory before parsing and raises ``MemoryPressureError``
if insufficient memory is available.
"""

import logging
from pathlib import Path
from typing import Tuple, Any, Dict, List

from beancount import loader

from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

# Thresholds (in MB)
MEMORY_HEADROOM_MB = 1024   # hard limit — reject parse
MEMORY_WARNING_MB = 2048    # soft limit — log warning


class MemoryPressureError(APIError):
    def __init__(self):
        super().__init__(
            message="Server memory low, please retry shortly",
            code=ec.MEMORY_PRESSURE,
            status_code=503,
        )


def load_ledger_checked(
    ledger_file: Path | str,
) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
    """Parse a Beancount ledger file with a memory guard.

    This is the single entry point for ``loader.load_file()`` across the
    codebase.  All code that needs parsed entries must call this instead
    of ``loader.load_file()`` directly.

    Returns:
        (entries, errors, options) tuple from Beancount's loader.

    Raises:
        MemoryPressureError: if available memory is below the hard threshold.
    """
    try:
        import psutil
        available_mb = psutil.virtual_memory().available / (1024 * 1024)

        if available_mb < MEMORY_HEADROOM_MB:
            logger.error(
                "Memory critical: %dMB available, rejecting ledger parse "
                "(hard limit: %dMB)",
                int(available_mb), MEMORY_HEADROOM_MB,
            )
            raise MemoryPressureError()

        if available_mb < MEMORY_WARNING_MB:
            logger.warning(
                "Memory headroom low: %dMB available "
                "(warning threshold: %dMB, hard limit: %dMB)",
                int(available_mb), MEMORY_WARNING_MB, MEMORY_HEADROOM_MB,
            )
    except ImportError:
        # psutil not installed — skip memory check (desktop mode)
        pass

    return loader.load_file(str(ledger_file))
