# ADR-0013: Replace by atomic swap; the external-editor race is accepted with detection

This record decides how a write detects an outside program that saves the same file at the same moment.

- **Status:** Accepted. Amended on 2026-10-29 (S0060): a ledger file with a second hard link refuses the save; what the swap keeps of a file, and what it does not.
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

## Amendment (2026-10-29): hard-linked files, and what the swap keeps (S0060)

- **Deciders:** owner, 2026-10-29, in session, relayed by the orchestrator, answering `design/inbox/answered/20261029-hard-linked-ledger-files.md`: "Refuse to save it (Recommended)". The option as shown: "Before writing anything, a ledger file with more than one hard link refuses the save with a message naming the file: remove the extra link or copy the file first. Reads and reports still work. Simple, fails closed, can be lifted later."
- **Why:** the swap gives the name a new file, so a second hard link to a ledger file would keep the old bytes without anyone being told (S0060 data-safety red team, finding 1). Options 2 (save and warn) and 3 (write in place, losing the swap's crash safety) were rejected by that answer.
- **Decision:** before the save writes anything, a ledger file it replaces whose link count is above 1 refuses the save, naming the file ("not saved", the ledger unchanged after the re-hash). Reads, reports and the other files are not affected. It is checked again when the file is backed up. One function holds the rule (`ledger_engine.ledger.writer._hard_links`).
- **Also, by the same reasoning (agent):** the swap keeps the file's mode, extended attributes (Finder tags) and ACL (copied to the temp before the swap; `fcopyfile` on macOS). It does not keep the owner (the new file belongs to the user running Finzytrack, who owns their ledger files), the BSD flags (`chflags hidden` and the like) or the inode number. A locked file (`uchg`, append-only) refuses the save before anything is written. These are stated as limits in `design/specs/engine-jobs.md` §15.
- **Unchanged:** the swap, the displaced-file check and everything else in this record.

## References

review:D1 §13.2; review:25 §8.3, §14 item 5; review:27 M1 risks; ADR-0008, ADR-0012; INV-01; CAP-DATA-EXTERNAL-EDIT.
