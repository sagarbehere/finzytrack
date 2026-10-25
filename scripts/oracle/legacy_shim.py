"""pytest plugin for S12: run the legacy backend's API-level tests against the legacy app as a separate process.

Loaded with `-p legacy_shim` into a COPY of the legacy test suite (`legacy_api_tests.py` sets it up).
FT_SHIM_MODE=record: run everything in process, and write the API-level node IDs (tests that use a client fixture).
FT_SHIM_MODE=process: run only the API-level tests; the conftest's `_create_test_client(root)` starts the legacy
app on the test's temp root (through `launch_legacy.LegacyApp`, offline) and returns an HTTP client for it. The node
IDs the shim really served are written out, so a test whose module builds its own in-process client is not
counted as running out of process.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.environ["FT_ORACLE_DIR"])
from launch_legacy import LegacyApp  # noqa: E402

CLIENT_FIXTURES = frozenset({"test_client", "edge_case_client", "multi_file_client", "multi_file_client_fresh", "client"})
MODE = os.environ.get("FT_SHIM_MODE", "record")
OUT = Path(os.environ.get("FT_SHIM_OUT", "."))
_current: dict[str, str] = {}
_served: set[str] = set()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProcessClient:
    """`with ProcessClient(root) as client:` — the legacy app on *root* plus an httpx client pointed at it."""

    def __init__(self, root: Path) -> None:
        self.app = LegacyApp(Path(root), free_port())
        self.client: httpx.Client | None = None

    def __enter__(self) -> httpx.Client:
        self.app.start()
        _served.add(_current.get("nodeid", "?"))
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.app.port}", timeout=120, trust_env=False)
        return self.client

    def __exit__(self, *exc: Any) -> None:
        if self.client:
            self.client.close()
        self.app.stop()


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    api = [i for i in items if CLIENT_FIXTURES & set(getattr(i, "fixturenames", ()))]
    if MODE == "record":
        (OUT / "api_tests.txt").write_text("".join(i.nodeid + "\n" for i in api), encoding="utf-8")
        return
    config.hook.pytest_deselected(items=[i for i in items if i not in api])
    items[:] = api
    patched = 0
    for name, mod in list(sys.modules.items()):
        if name.endswith("conftest") and hasattr(mod, "_create_test_client"):
            mod._create_test_client = ProcessClient
            patched += 1
    if not patched:
        raise pytest.UsageError("legacy_shim: no conftest defines _create_test_client; the shim has nothing to patch")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _current["nodeid"] = item.nodeid


def pytest_sessionfinish(session: pytest.Session) -> None:
    if MODE == "process":
        (OUT / "shimmed.txt").write_text("".join(n + "\n" for n in sorted(_served)), encoding="utf-8")
