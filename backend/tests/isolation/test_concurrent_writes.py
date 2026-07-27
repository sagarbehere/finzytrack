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
import time
from datetime import date
from pathlib import Path

import pytest

from app.core.backup_manager import BackupManager
from app.core.ledger_initializer import LedgerInitializer
from app.core.ledger_manager import LedgerManager
from app.write_lock import WriteLockManager
from app.schemas.account_schemas import AccountCreateRequest


ALICE = {"X-User-ID": "alice"}


def _manager_with_lock(config) -> LedgerManager:
    """A LedgerManager wired with a real (file-backed) WriteLockManager,
    mirroring production wiring (service_factory)."""
    backup_manager = BackupManager(
        backup_dir=Path(config.backup_dir),
        retention_count=config.backup.retention_count,
    )
    ledger_initializer = LedgerInitializer(
        ledger_file=config.ledger_file,
        default_currency=config.accounts.default_currency,
        backup_manager=backup_manager,
    )
    write_lock = WriteLockManager(
        user_id="local", lock_file=Path(config.write_lock_path)
    )
    return LedgerManager(
        ledger_file=config.ledger_file,
        backup_manager=backup_manager,
        ledger_initializer=ledger_initializer,
        write_lock=write_lock,
    )


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


class TestLostUpdatePrevention:
    """The write lock must cover the whole read-modify-write, not just the
    file write, so a concurrent mutation can't clobber another's update.

    Spec: given two concurrent mutations of the same ledger, both must
    persist. This test *forces* the interleaving that produces a lost
    update when the parse happens outside the lock (the pre-fix bug):
    thread A is held inside its write step while thread B runs its entire
    mutation. With parse-outside-lock, B parses the pre-A snapshot and its
    rewrite drops A's account. With parse-inside-lock, B blocks until A is
    fully done. This test fails on the old code and passes on the fixed code.
    """

    def test_forced_interleave_preserves_both_updates(self, config, monkeypatch):
        mgr = _manager_with_lock(config)

        # Widen A's critical section: pause inside the actual file write,
        # signalling once A is in. Under the fix, A holds the lock across its
        # parse+write for this whole window, so B cannot parse a stale snapshot.
        orig_do_write = mgr._do_write_entries
        entered_write = threading.Event()

        def slow_write(entries, options=None):
            entered_write.set()
            time.sleep(0.4)
            return orig_do_write(entries, options)

        monkeypatch.setattr(mgr, "_do_write_entries", slow_write)

        errors: dict[str, str] = {}

        def make(name: str, key: str):
            try:
                mgr.create_account_directive(
                    AccountCreateRequest(
                        name=name, open_date=date(2024, 1, 1), currencies=["USD"]
                    )
                )
            except Exception as e:  # noqa: BLE001 - surface any failure to the assert
                errors[key] = repr(e)

        t_a = threading.Thread(target=make, args=("Assets:RaceA", "a"))
        t_a.start()
        assert entered_write.wait(timeout=5), "thread A never reached the write step"

        # A is now inside its write window. Start B; under the old code its
        # parse would race here and lose A's update.
        t_b = threading.Thread(target=make, args=("Assets:RaceB", "b"))
        t_b.start()

        t_a.join(timeout=10)
        t_b.join(timeout=10)
        assert not t_a.is_alive() and not t_b.is_alive(), "a thread deadlocked"
        assert not errors, f"unexpected errors: {errors}"

        # Fresh parse from disk — both accounts must have survived.
        entries, _, _ = mgr._parse_ledger()
        opened = {
            e.account for e in entries if type(e).__name__ == "Open"
        }
        assert "Assets:RaceA" in opened, "thread A's update was lost"
        assert "Assets:RaceB" in opened, "thread B's update was lost"
