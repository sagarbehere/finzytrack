"""
Multi-user data isolation tests.

Specification under test:
    "In a multi-user hosted system, one user must never see another
    user's data.  Each user's accounts, transactions, config, and
    rules are completely isolated."

Every test in this module follows the same pattern:
    1. Act as user A (mutate a resource).
    2. Read as user B.
    3. Assert user B's state is unaffected by user A's mutation.
    4. Verify via subsequent read — never trust the mutation response alone.

Tests are written from the isolation specification.  The question each
test answers is: "If the implementation were broken in way X, would
this test catch it?"  Common broken implementations to guard against:

- Both users sharing the same underlying file (one write affects both reads)
- The header being ignored and every request hitting a single set of services
- Path resolution producing the same directory for different user IDs
"""

import pytest


ALICE = {"X-User-ID": "alice"}
BOB = {"X-User-ID": "bob"}


# ── Auth enforcement ────────────────────────────────────────────────────────


class TestAuthEnforcement:
    """In hosted mode, every request must identify the user."""

    def test_missing_user_id_returns_401(self, hosted_client):
        """Spec: unauthenticated requests must be rejected with 401."""
        resp = hosted_client.get("/api/accounts")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTH_REQUIRED"

    def test_invalid_user_id_format_returns_400(self, hosted_client):
        """Spec: user IDs must be alphanumeric/hyphens/underscores, max 64 chars.
        A path-traversal-style ID must be rejected before it reaches any service."""
        resp = hosted_client.get("/api/accounts", headers={"X-User-ID": "../escape"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_USER_ID"

    def test_empty_user_id_returns_401(self, hosted_client):
        """Spec: an empty X-User-ID is the same as missing — reject with 401."""
        resp = hosted_client.get("/api/accounts", headers={"X-User-ID": ""})
        assert resp.status_code == 401

    def test_valid_user_id_succeeds(self, hosted_client):
        """Spec: a well-formed X-User-ID must be accepted."""
        resp = hosted_client.get("/api/accounts", headers=ALICE)
        assert resp.status_code == 200


# ── Account isolation ───────────────────────────────────────────────────────


class TestAccountIsolation:
    """Spec: User A's accounts must not appear in user B's account list."""

    def test_created_account_invisible_to_other_user(self, hosted_client):
        """Alice creates an account. Bob must not see it.
        We verify by reading Bob's FULL account list and asserting
        the new name is absent — not just checking a count."""
        # Snapshot Bob's accounts BEFORE Alice acts
        bob_before = hosted_client.get("/api/accounts", headers=BOB)
        bob_names_before = {a["name"] for a in bob_before.json()["data"]["accounts"]}

        # Alice creates a unique account
        create_resp = hosted_client.post(
            "/api/accounts",
            json={
                "name": "Expenses:AlicePrivate",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
            },
            headers=ALICE,
        )
        assert create_resp.status_code in (200, 201)

        # Verify Alice can see it via subsequent read
        alice_resp = hosted_client.get("/api/accounts", headers=ALICE)
        alice_names = {a["name"] for a in alice_resp.json()["data"]["accounts"]}
        assert "Expenses:AlicePrivate" in alice_names

        # Bob's account list must be UNCHANGED — same names as before
        bob_after = hosted_client.get("/api/accounts", headers=BOB)
        bob_names_after = {a["name"] for a in bob_after.json()["data"]["accounts"]}
        assert bob_names_after == bob_names_before, (
            f"Bob's accounts changed after Alice's mutation. "
            f"New entries: {bob_names_after - bob_names_before}"
        )

    def test_users_start_with_independent_data(self, hosted_client):
        """Even though both users are seeded from the same fixture,
        mutating one must not affect the other — proving they have
        separate underlying files, not a shared reference."""
        # Alice deletes an account that exists in the seed fixture
        alice_resp = hosted_client.get("/api/accounts", headers=ALICE)
        alice_accounts = alice_resp.json()["data"]["accounts"]
        # Pick the first account that exists
        if not alice_accounts:
            pytest.skip("No seed accounts to test with")
        target_name = alice_accounts[0]["name"]

        hosted_client.delete(
            f"/api/accounts/{target_name}",
            headers=ALICE,
        )

        # Verify Alice no longer has it
        alice_after = hosted_client.get("/api/accounts", headers=ALICE)
        alice_names_after = {a["name"] for a in alice_after.json()["data"]["accounts"]}
        assert target_name not in alice_names_after

        # Bob must STILL have it — his ledger is a separate file
        bob_resp = hosted_client.get("/api/accounts", headers=BOB)
        bob_names = {a["name"] for a in bob_resp.json()["data"]["accounts"]}
        assert target_name in bob_names, (
            f"Bob lost account '{target_name}' after Alice deleted it — "
            f"they may be sharing the same ledger file"
        )


# ── Config isolation ────────────────────────────────────────────────────────


class TestConfigIsolation:
    """Spec: Config changes by user A must not affect user B."""

    def test_config_mutation_does_not_cross_users(self, hosted_client):
        """Alice changes her default currency. Bob's must remain unchanged."""
        # Read Bob's currency BEFORE Alice acts
        bob_before = hosted_client.get("/api/config", headers=BOB)
        assert bob_before.status_code == 200
        bob_currency_before = bob_before.json()["data"]["accounts"]["default_currency"]

        # Alice changes her currency
        hosted_client.patch(
            "/api/config",
            json={"accounts": {"default_currency": "EUR"}},
            headers=ALICE,
        )

        # Verify Alice's change took effect
        alice_resp = hosted_client.get("/api/config", headers=ALICE)
        assert alice_resp.json()["data"]["accounts"]["default_currency"] == "EUR"

        # Bob's currency must be UNCHANGED
        bob_after = hosted_client.get("/api/config", headers=BOB)
        bob_currency_after = bob_after.json()["data"]["accounts"]["default_currency"]
        assert bob_currency_after == bob_currency_before, (
            f"Bob's currency changed from {bob_currency_before} to "
            f"{bob_currency_after} after Alice's config mutation"
        )


# ── Rule isolation ──────────────────────────────────────────────────────────


# Minimal valid CSV rule YAML — uses the same structure as the seed rules.
_MINIMAL_CSV_RULE = """\
name: "Alice Private Bank"
separator: ","
encoding: "utf-8"
skip_lines_start: 0
skip_lines_end: 0
date_format: "%Y-%m-%d"
decimal_separator: "."
columns:
  date: 0
  payee: 1
  amount: 2
default_account: "Assets:AliceBank"
default_currency: "USD"
"""


class TestRuleIsolation:
    """Spec: Each user's import rules are completely isolated."""

    def test_created_csv_rule_invisible_to_other_user(self, hosted_client):
        """Alice creates a CSV rule. Bob must not see it.

        This test catches the case where both users' CsvRulesManagers
        point at the same rules directory — Alice's rule file would
        appear in Bob's rule listing."""
        # Snapshot Bob's rules BEFORE Alice acts
        bob_before = hosted_client.get("/api/import/csv-rules", headers=BOB)
        assert bob_before.status_code == 200
        bob_rules_before = {
            r["filename"] for r in bob_before.json()["data"]["rules"]
        }

        # Alice creates a rule
        create_resp = hosted_client.post(
            "/api/import/csv-rules",
            json={"filename": "alice-private-bank.yaml", "content": _MINIMAL_CSV_RULE},
            headers=ALICE,
        )
        assert create_resp.status_code in (200, 201), (
            f"Alice's rule creation failed: {create_resp.status_code} "
            f"{create_resp.json()}"
        )

        # Verify Alice can see it via subsequent read
        alice_resp = hosted_client.get("/api/import/csv-rules", headers=ALICE)
        alice_rules = {r["filename"] for r in alice_resp.json()["data"]["rules"]}
        assert "alice-private-bank.yaml" in alice_rules, (
            "Alice cannot see her own rule — creation may have failed silently"
        )

        # Bob's rule list must be UNCHANGED
        bob_after = hosted_client.get("/api/import/csv-rules", headers=BOB)
        bob_rules_after = {
            r["filename"] for r in bob_after.json()["data"]["rules"]
        }
        assert bob_rules_after == bob_rules_before, (
            f"Bob's rules changed after Alice created a rule. "
            f"New entries: {bob_rules_after - bob_rules_before}"
        )
