"""
Concurrent write serialisation tests.

Specification under test:
    "Concurrent write requests to the same user's ledger must both
    succeed without data corruption.  The write lock serialises them —
    one completes, then the other runs."

Note on concurrency with TestClient: FastAPI's TestClient runs a WSGI
transport, so true concurrent I/O overlap is limited.  These tests
verify that the threading.Lock does not deadlock or lose writes when
multiple threads call into the same LedgerManager.  They are a
correctness check, not a stress test.
"""

import threading

import pytest


ALICE = {"X-User-ID": "alice"}


class TestConcurrentWrites:
    """Two simultaneous writes to the same user must both succeed."""

    def test_concurrent_account_creation_preserves_both(self, hosted_client):
        """Spec: two concurrent account creations must both persist.

        After both complete, we read the full account list and assert
        BOTH new accounts are present AND that no pre-existing accounts
        were lost (data corruption check).
        """
        # Snapshot existing accounts before the concurrent writes
        before_resp = hosted_client.get("/api/accounts", headers=ALICE)
        assert before_resp.status_code == 200
        names_before = {a["name"] for a in before_resp.json()["data"]["accounts"]}

        results = {}
        errors = {}

        def create_account(name: str, key: str):
            try:
                resp = hosted_client.post(
                    "/api/accounts",
                    json={
                        "name": name,
                        "open_date": "2024-01-01",
                        "currencies": ["USD"],
                    },
                    headers=ALICE,
                )
                results[key] = resp.status_code
            except Exception as e:
                errors[key] = str(e)

        t1 = threading.Thread(
            target=create_account,
            args=("Expenses:ConcurrentA", "a"),
        )
        t2 = threading.Thread(
            target=create_account,
            args=("Expenses:ConcurrentB", "b"),
        )

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Neither thread should have crashed
        assert not errors, f"Thread(s) raised exceptions: {errors}"

        # Both requests must have succeeded
        assert results.get("a") in (200, 201), f"Thread A status: {results.get('a')}"
        assert results.get("b") in (200, 201), f"Thread B status: {results.get('b')}"

        # Read the full account list AFTER both writes
        after_resp = hosted_client.get("/api/accounts", headers=ALICE)
        assert after_resp.status_code == 200
        names_after = {a["name"] for a in after_resp.json()["data"]["accounts"]}

        # Both new accounts must be present
        assert "Expenses:ConcurrentA" in names_after, "Account from thread A is missing"
        assert "Expenses:ConcurrentB" in names_after, "Account from thread B is missing"

        # All pre-existing accounts must still be present (no data corruption)
        lost = names_before - names_after
        assert not lost, (
            f"Pre-existing accounts were lost after concurrent writes: {lost}"
        )
