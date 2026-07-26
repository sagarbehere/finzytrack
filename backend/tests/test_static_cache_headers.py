"""Caching headers for the served SPA.

`index.html` is the one file whose URL never changes while its contents change
every release — it names the content-hashed asset bundles. If a webview is left
to its own caching heuristics it can reuse the previous release's shell against
the new backend, which silently skips the entire upgrade flow: detecting pending
migrations is client-initiated (the frontend calls /api/startup/tasks), so a stale
shell never asks and nothing is ever offered. Observed on macOS upgrading
v0.1.4 → v0.2.0 — the new backend served zero requests for `/` or any asset.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def spa_client(config, tmp_path) -> TestClient:
    """A client backed by a minimal frontend build (index.html + hashed asset)."""
    static = tmp_path / "frontend_dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (static / "assets" / "index-abc123.js").write_text("// app", encoding="utf-8")
    return TestClient(create_app(config, static_dir=str(static)))


@pytest.mark.parametrize("path", ["/", "/dashboard"])
def test_index_is_never_cached(spa_client: TestClient, path: str):
    """Both the root and the SPA fallback (a client-side route on hard refresh)
    must forbid caching — a stale shell from either entry point is the bug."""
    resp = spa_client.get(path)
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")


def test_hashed_assets_stay_cacheable(spa_client: TestClient):
    """Assets are content-addressed by filename, so caching them is safe and
    desirable — a new build requests new names. Only the shell is uncacheable."""
    resp = spa_client.get("/assets/index-abc123.js")
    assert resp.status_code == 200
    assert "no-store" not in resp.headers.get("cache-control", "")
