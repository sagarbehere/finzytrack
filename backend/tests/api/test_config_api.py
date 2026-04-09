"""
API tests for /api/config endpoints.

Tests the HTTP contract for configuration retrieval and patching.
"""

import pytest


class TestGetConfig:
    """GET /api/config — retrieve current configuration."""

    def test_returns_success_envelope(self, test_client):
        resp = test_client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None

    def test_config_has_expected_sections(self, test_client):
        resp = test_client.get("/api/config")
        config = resp.json()["data"]
        assert "setup_complete" in config
        assert "ledger_file" in config
        assert "server" in config
        assert "accounts" in config
        assert "backup" in config
        assert "ai" in config

    def test_default_currency_is_usd(self, test_client):
        """Our test fixtures use USD as the default currency."""
        resp = test_client.get("/api/config")
        accounts = resp.json()["data"]["accounts"]
        assert accounts["default_currency"] == "USD"

    def test_setup_complete_is_true(self, test_client):
        """Test client is created with setup_complete=True."""
        resp = test_client.get("/api/config")
        assert resp.json()["data"]["setup_complete"] is True


class TestPatchConfig:
    """PATCH /api/config — partially update configuration."""

    def test_patch_returns_success(self, test_client):
        resp = test_client.patch(
            "/api/config",
            json={"accounts": {"default_currency": "EUR"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_patch_updates_value(self, test_client):
        test_client.patch(
            "/api/config",
            json={"accounts": {"default_unknown_account": "Expenses:Other"}},
        )
        resp = test_client.get("/api/config")
        assert resp.json()["data"]["accounts"]["default_unknown_account"] == "Expenses:Other"

    def test_patch_response_includes_restart_info(self, test_client):
        """Patch response should indicate whether a restart is needed."""
        resp = test_client.patch(
            "/api/config",
            json={"accounts": {"default_currency": "GBP"}},
        )
        data = resp.json()["data"]
        assert "restart_required" in data
        assert isinstance(data["restart_required"], bool)

    def test_patch_server_settings_requires_restart(self, test_client):
        """Changing server host/port should flag restart_required."""
        resp = test_client.patch(
            "/api/config",
            json={"server": {"port": 9999}},
        )
        data = resp.json()["data"]
        assert data["restart_required"] is True


class TestHealthEndpoint:
    """GET /health — application health check."""

    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
