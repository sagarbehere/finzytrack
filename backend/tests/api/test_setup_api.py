"""
API tests for /api/setup endpoints.

Tests the first-run setup wizard. These use a fresh root directory
with setup_complete=False (unlike other test fixtures which start
with setup_complete=True).
"""

import shutil
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Config

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def setup_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """TestClient with setup_complete=False — simulates a fresh install."""
    from app.main import create_app

    root = tmp_path
    (root / "config" / "csv_rules").mkdir(parents=True)
    (root / "config" / "xls_rules").mkdir(parents=True)
    (root / "config" / "email_rules").mkdir(parents=True)
    (root / "config" / "recipes").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)

    # Write a minimal config with setup_complete=false
    config_yaml = root / "config" / "config.yaml"
    config_yaml.write_text(
        "setup_complete: false\n"
        "accounts:\n"
        "  default_currency: USD\n"
    )

    config = Config(
        root_dir=root,
        setup_complete=False,
        config_file_path=config_yaml,
    )

    app = create_app(config)
    with TestClient(app) as client:
        yield client


class TestSetupWizard:
    """POST /api/setup/complete — first-run setup."""

    def test_fresh_setup_succeeds(self, setup_client):
        """A fresh setup with a new ledger should succeed."""
        resp = setup_client.post(
            "/api/setup/complete",
            json={"currency": "USD", "ledger_mode": "fresh"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["config"]["setup_complete"] is True

    def test_setup_sets_currency(self, setup_client):
        """The chosen currency should be reflected in the config."""
        resp = setup_client.post(
            "/api/setup/complete",
            json={"currency": "EUR", "ledger_mode": "fresh"},
        )
        assert resp.status_code == 200
        config = resp.json()["data"]["config"]
        assert config["accounts"]["default_currency"] == "EUR"

    def test_setup_cannot_run_twice(self, setup_client):
        """After setup completes, calling it again should fail."""
        setup_client.post(
            "/api/setup/complete",
            json={"currency": "USD", "ledger_mode": "fresh"},
        )
        resp = setup_client.post(
            "/api/setup/complete",
            json={"currency": "USD", "ledger_mode": "fresh"},
        )
        assert resp.status_code != 200
        assert resp.json()["success"] is False

    def test_setup_redirected_before_complete(self, setup_client):
        """Before setup, the config should show setup_complete=false."""
        resp = setup_client.get("/api/config")
        assert resp.status_code == 200
        assert resp.json()["data"]["setup_complete"] is False
