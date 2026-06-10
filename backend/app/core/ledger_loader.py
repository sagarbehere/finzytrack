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
from beancount.parser import parser as bc_parser

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


def discover_includes_per_file(root_path: str | Path) -> Dict[str, List[str]]:
    """Walk the include tree starting at ``root_path`` and return a map of
    ``{absolute_filename: [absolute_path_of_each_file_it_includes, ...]}``.

    Each file is parsed individually via ``bc_parser.parse_file`` so we only
    see *that file's own* ``include`` directives, not the flat union that the
    full loader returns in ``options['include']``. This lets the write path
    preserve nested includes (root → A → B) by re-emitting A's own include
    of B when A is rewritten.

    The traversal is breadth-first and tolerates missing/unreadable child
    files (logs and skips, rather than raising) — write-path callers want a
    best-effort map and will fall back to re-emitting only the root's
    includes if traversal partially fails.
    """
    result: Dict[str, List[str]] = {}
    root_abs = str(Path(root_path).resolve())
    stack: List[str] = [root_abs]
    seen: set[str] = set()

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        current_path = Path(current)
        if not current_path.is_file():
            logger.debug("discover_includes_per_file: %s missing or not a file", current)
            result[current] = []
            continue

        try:
            _, _, opts = bc_parser.parse_file(current)
        except Exception as e:
            logger.warning("discover_includes_per_file: parse_file(%s) failed: %s", current, e)
            result[current] = []
            continue

        own_includes_raw: List[str] = list(opts.get('include') or [])
        own_includes_abs: List[str] = []
        for inc in own_includes_raw:
            inc_path = Path(inc)
            if not inc_path.is_absolute():
                inc_path = current_path.parent / inc_path
            abs_inc = str(inc_path.resolve())
            own_includes_abs.append(abs_inc)
            if abs_inc not in seen:
                stack.append(abs_inc)

        result[current] = own_includes_abs

    return result

