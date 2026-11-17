"""The switch that selects the Windows file-system adapter for a test (S0128 round 1, verifier F1). **Tests only:** no
product module may import this module, name what it holds, or reach it another way
(`tests/architecture/test_platform_selection.py`): `ledger_engine.fs.this_os` reads it, and only on Windows, so the
product on Windows refuses every file operation until BL-0087 turns writes on.

It is its own module so that the guard can refuse it by its import, whatever spelling a product module would use for
the names in it; the guard also refuses `contextvars` in the product outside the path model and this module (iterating
a copied context reaches every context variable) and any reflective access (`getattr`, `setattr`, `vars`,
`__dict__`) to this module or to `this_os`.
"""

from __future__ import annotations

import contextvars
import errno
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

ON: Final = contextvars.ContextVar[bool]("windows_port_under_test", default=False)


def selected() -> bool:
    """Whether a test has selected the Windows adapter in this context (always False off Windows)."""
    return sys.platform == "win32" and ON.get()


@contextmanager
def windows_port_under_test() -> Generator[None]:
    """Tests only: on Windows, the Windows adapter is selected in this context until the block ends, so a test runs
    the product's own code paths on it. Off Windows it raises `OSError` (`ENOTSUP`)."""
    if sys.platform != "win32":
        raise OSError(errno.ENOTSUP, "the Windows adapter exists only on Windows")
    token = ON.set(True)
    try:
        yield
    finally:
        ON.reset(token)
