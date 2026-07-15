"""API tests for /api/dashboard-theme endpoints.

The active-theme endpoint resolves against the bundled seed (Dusty Spectrum),
so it works even when the test config dir has no themes copied in. Writes are
verified via a subsequent read (round-trip). Assertions are on error *codes*,
not message strings (per backend/CLAUDE.md testing rules).
"""


def _valid_theme(theme_id: str = "test-theme") -> dict:
    """A minimal but schema-valid dashboard theme payload."""
    def mode(l, d):
        return {"light": l, "dark": d}
    return {
        "id": theme_id,
        "name": "Test Theme",
        "description": "A theme for tests.",
        "brand": mode("#4f6bb0", "#7b93d6"),
        "baseline": mode("#9aa5b1", "#8a95a3"),
        "valence": {
            "good": mode("#4e8f66", "#5faf7f"),
            "warn": mode("#b8863a", "#d8a24a"),
            "bad": mode("#bf5b52", "#d97066"),
            "complete": mode("#3f8d82", "#56b0a4"),
        },
        "categorical": {
            "light": ["#4f6bb0", "#8a6bb0", "#b06b93"],
            "dark": ["#7f9ad8", "#a98fd0", "#cf8fb0"],
        },
    }


class TestGetActiveTheme:
    def test_returns_active_theme(self, test_client):
        resp = test_client.get("/api/dashboard-theme")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        theme = body["data"]
        assert theme["id"] == "dusty-spectrum"
        assert theme["brand"]["light"] and theme["brand"]["dark"]
        assert set(theme["valence"].keys()) == {"good", "warn", "bad", "complete"}
        assert len(theme["categorical"]["dark"]) == 12
        assert len(theme["categorical"]["light"]) == 12

    def test_never_404s(self, test_client):
        """Active resolves to a real theme even with nothing user-side."""
        assert test_client.get("/api/dashboard-theme").status_code == 200


class TestListThemes:
    def test_lists_bundled_default(self, test_client):
        resp = test_client.get("/api/dashboard-themes")
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = [t["id"] for t in data["themes"]]
        assert "dusty-spectrum" in ids
        assert data["active"] == "dusty-spectrum"


class TestGetThemeById:
    def test_get_existing(self, test_client):
        resp = test_client.get("/api/dashboard-theme/dusty-spectrum")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "dusty-spectrum"

    def test_get_missing_is_404(self, test_client):
        resp = test_client.get("/api/dashboard-theme/no-such-theme")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "DASHBOARD_THEME_NOT_FOUND"


class TestWriteTheme:
    def test_write_then_read_roundtrip(self, test_client):
        payload = _valid_theme("roundtrip-theme")
        resp = test_client.put(
            "/api/dashboard-theme/roundtrip-theme", json={"content": payload}
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify via a subsequent read.
        got = test_client.get("/api/dashboard-theme/roundtrip-theme")
        assert got.status_code == 200
        theme = got.json()["data"]
        assert theme["id"] == "roundtrip-theme"
        assert theme["name"] == "Test Theme"
        assert theme["categorical"]["dark"] == ["#7f9ad8", "#a98fd0", "#cf8fb0"]

    def test_invalid_content_is_validation_error(self, test_client):
        bad = _valid_theme("bad-theme")
        del bad["brand"]  # required
        resp = test_client.put(
            "/api/dashboard-theme/bad-theme", json={"content": bad}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "DASHBOARD_THEME_VALIDATION_ERROR"

    def test_id_mismatch_is_validation_error(self, test_client):
        payload = _valid_theme("one-id")
        resp = test_client.put(
            "/api/dashboard-theme/different-id", json={"content": payload}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "DASHBOARD_THEME_VALIDATION_ERROR"
