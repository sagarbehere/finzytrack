# ADR-0013: Replace by atomic swap; the external-editor race is accepted with detection

This record decides how a write detects an outside program that saves the same file at the same moment.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path, data safety

## Context

An editor, Fava or a sync client may save a ledger file between our final fingerprint check (under our lock) and our replace. Such tools do not take our locks. Read-back after `os.replace` cannot see it, because `rename` unlinks the displaced inode and its bytes are gone. Full prevention would require locking the user's editor out.

## Decision

(owner, 2026-10-24) **Accept the race, keep its window minimal, and detect it.**
- Re-check the fingerprint immediately before each replace, under the lock.
- **Replace by atomic swap:** macOS `renamex_np(RENAME_SWAP)`, Linux `renameat2(RENAME_EXCHANGE)`, Windows `ReplaceFileW` with a backup name. Then hash the displaced file and compare it with the planned pre-hash.
- **On a mismatch,** an outside save landed: its bytes are kept, and the user is told ("Your editor saved this file at the same moment. Review this file."), with the backup available.
- **Focus check:** when the window regains focus, a file that differs from our last post-hash gets the notice "A change you made in Finzytrack was overwritten by another program", with the backup.
- Where swap is unavailable, the documented window remains, and the user docs say so.

## Alternatives considered and rejected

- **Plain `os.replace` with read-back.** Rejected: it cannot detect the race.
- **Locking the user's editor out of the file.** Rejected: not possible without the editor's cooperation.
- **Ignoring the race.** Rejected: a silent lost edit violates priority #1.

## Consequences

- The TLA+ property is `ExternalEditNeverSilentlyLost`.
- The swap primitive is OS-specific code in the file-system adapter, tested on all three OSes in the packaged matrix.

## References

review:D1 §13.2; review:25 §8.3, §14 item 5; review:27 M1 risks; ADR-0008, ADR-0012; INV-01; CAP-DATA-EXTERNAL-EDIT.
