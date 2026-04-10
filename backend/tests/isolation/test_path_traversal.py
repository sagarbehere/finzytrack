"""
Path traversal prevention tests.

Specification under test:
    "A user must never be able to read or write files outside their own
    directory tree."

Each test creates a known file or directory structure and verifies that
the guard rejects access.  We assert on the SPECIFIC status code (403)
to prove the path guard fired — not 404 which could mean a different
check caught it first, masking a missing guard.
"""

import pytest

from app.app_mode import AppMode


ALICE = {"X-User-ID": "alice"}
BOB = {"X-User-ID": "bob"}


class TestFilesystemBrowseJail:
    """In hosted mode, browsing must be restricted to the user's root directory."""

    def test_absolute_path_outside_root_rejected(self, hosted_client):
        """Spec: /tmp exists and is readable, but it is outside Alice's root.
        The response MUST be 403, not 200."""
        resp = hosted_client.get(
            "/api/filesystem/browse",
            params={"path": "/tmp"},
            headers=ALICE,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for /tmp (outside user root), got {resp.status_code}"
        )

    def test_browsing_own_root_succeeds(self, hosted_client, hosted_tmp_root):
        """Spec: Alice CAN browse her own directory."""
        alice_root = hosted_tmp_root / "users" / "alice"
        resp = hosted_client.get(
            "/api/filesystem/browse",
            params={"path": str(alice_root)},
            headers=ALICE,
        )
        assert resp.status_code == 200

    def test_browsing_other_user_root_rejected(self, hosted_client, hosted_tmp_root):
        """Spec: Alice must not browse Bob's directory.
        Bob's directory exists and is readable, so a 404 would be wrong —
        it must be 403."""
        bob_root = hosted_tmp_root / "users" / "bob"
        assert bob_root.exists(), "Test precondition: Bob's directory must exist"
        resp = hosted_client.get(
            "/api/filesystem/browse",
            params={"path": str(bob_root)},
            headers=ALICE,
        )
        assert resp.status_code == 403, (
            f"Alice accessed Bob's directory with status {resp.status_code} — "
            f"expected 403"
        )

    def test_parent_traversal_cannot_reach_other_user(self, hosted_client, hosted_tmp_root):
        """Spec: navigating up via parent_path from Alice's root must not
        reach Bob's directory.  We resolve Alice's root, go up two levels
        (to the base_dir), and try to reach Bob."""
        bob_root = hosted_tmp_root / "users" / "bob"
        # Construct a path that goes up from Alice's root and into Bob's
        traversal_path = str(hosted_tmp_root / "users" / "alice" / ".." / "bob")
        resp = hosted_client.get(
            "/api/filesystem/browse",
            params={"path": traversal_path},
            headers=ALICE,
        )
        assert resp.status_code == 403, (
            f"Path traversal to Bob's directory succeeded with status {resp.status_code}"
        )


class TestRecipePathTraversal:
    """Recipe path guard must reject paths that resolve outside recipes/.

    Starlette normalises ``..`` segments in URLs before routing, so
    HTTP-level tests with ``../../`` never reach our recipe handler —
    the framework protects at the URL layer.  Our ``guard_path`` call
    is defense-in-depth against non-URL traversal vectors (symlinks,
    programmatic calls, future framework changes).

    We test it directly at the function level to prove it works.
    """

    def test_guard_path_rejects_escape_from_recipes(self, hosted_tmp_root):
        """Spec: a resolved path outside the recipes directory must
        be rejected by guard_path, regardless of how the path was
        constructed.

        We call guard_path directly because Starlette's URL
        normalisation prevents ``..`` from reaching the handler in
        HTTP tests — which is good (double protection), but means
        we can't test our guard via the HTTP endpoint."""
        from app.helpers.path_guard import guard_path
        from app.exceptions import APIError

        recipes_dir = hosted_tmp_root / "users" / "alice" / "config" / "recipes"
        # A path that resolves outside recipes/ but inside the user root
        escaped_path = (recipes_dir / ".." / "config.yaml").resolve()
        assert escaped_path.exists(), "Test precondition: target file must exist"

        with pytest.raises(APIError) as exc_info:
            guard_path(escaped_path, recipes_dir, "recipe path")
        assert exc_info.value.status_code == 403

    def test_guard_path_allows_path_within_recipes(self, hosted_tmp_root):
        """Sanity: a path within recipes/ must be allowed by guard_path."""
        from app.helpers.path_guard import guard_path

        recipes_dir = hosted_tmp_root / "users" / "alice" / "config" / "recipes"
        # Create a file inside recipes/
        test_file = recipes_dir / "test.json"
        test_file.write_text("{}")

        result = guard_path(test_file, recipes_dir, "recipe path")
        assert result == test_file.resolve()


class TestDesktopModeUnrestricted:
    """Desktop mode must NOT restrict filesystem browsing (file picker needs it)."""

    def test_desktop_browse_absolute_path_allowed(self, test_client):
        """Spec: in desktop mode, the file picker needs to browse the entire
        filesystem.  /tmp is a real, accessible directory — it must not
        return 403."""
        resp = test_client.get(
            "/api/filesystem/browse",
            params={"path": "/tmp"},
        )
        assert resp.status_code == 200, (
            f"Desktop mode rejected /tmp with {resp.status_code} — "
            f"file picker browsing should be unrestricted"
        )
