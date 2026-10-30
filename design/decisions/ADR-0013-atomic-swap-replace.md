# ADR-0013: Replace by atomic swap; the external-editor race is accepted with detection

This record decides how a write detects an outside program that saves the same file at the same moment.

- **Status:** Accepted. Amended on 2026-10-29 (S0060): a ledger file with a second hard link refuses the save; what the swap keeps of a file, and what it does not; volumes without the swap are refused until their fallback slice (BL-0160). Amended on 2026-10-29 (S0061): nothing is swapped back; an outside save a swap or a clean-up meets goes to a kept name beside the file.
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
- **What the swap keeps** (proposed by the agent; owner, 2026-10-29, approving this bullet: "Approve it (Recommended)", the option reading "The bullet stays in ADR-0013, adjusted to your ACL answer, with your approval quoted."; and answering what a save should keep: "Keep xattrs, drop ACLs (Recommended)", the option reading "Copy extended attributes (Finder tags) only; a file's ACL is not kept by a save (stated as a limit). A file whose ACL forbids the swap (deny delete) is refused before anything is written."): the swap keeps the file's mode and extended attributes (Finder tags; copied to the temp before the swap, `fcopyfile` with `COPYFILE_XATTR` on macOS). It does not keep the file's ACL, the owner (the new file belongs to the user running Finzytrack, who owns their ledger files), the BSD flags (`chflags hidden` and the like) or the inode number. A locked file (`uchg`, append-only), and a file or folder whose permissions or ACL would block the swap or its clean-up (a read-only ledger folder, deny delete on the file, deny delete_child on the folder, extended attributes that cannot be read), refuse the save before anything is written. These are stated as limits in `design/specs/engine-jobs.md` §15.
- **Volumes without the swap** (exFAT, FAT, some network shares: no `RENAME_SWAP` or `RENAME_EXCL`). Owner, 2026-10-29, answering what should happen there: "Unsafe fallback on such drives"; then, after the trade-offs and the proposal to build it as its own slice with an up-front refusal until then: "Agreed, fallback as its own slice after slice 4". So such a volume gets a non-atomic fallback write path in a later slice (BL-0160, after BL-0151's slice 4), and until it exists a save there is refused before anything is written (`ledger_engine.ledger.writer._volume_without_swap`, which reads the volume's capabilities).
- **Unchanged:** the swap, the displaced-file check and everything else in this record.

- **Code pointer, S0061** (no change to the decision): the two rules' one function each moved with the restore, which shares them with the save: `ledger_engine.ledger.handles.hard_links` (was `writer._hard_links`) and `ledger_engine.ledger.handles.volume_without_swap` (was `writer._volume_without_swap`). The restore applies the hard-link rule too: a file to restore with a second hard link stops the journal instead of being swapped.

## Amendment (2026-10-29): nothing is swapped back; outside saves go to a kept name (S0061)

- **Deciders:** owner, 2026-10-29, in session, relayed by the orchestrator, answering "Shall I go ahead with the simplification?": "Yes". The proposal as shown:

  > anything we displaced that isn't ours is moved at once to a "kept" name that nothing of ours ever deletes, and the save stops.
  > 1. Swap as today, then check what was displaced.
  > 2. If it is ours, or byte-identical to something we already hold (the pre bytes, which the backup has; or the post bytes we wrote), treat it as ours (deleting it loses nothing).
  > 3. Otherwise do not swap back: rename the displaced object straight to a visible kept name beside the file (e.g. `main.bean.kept-j7`), sync, and stop. Whatever now sits at `main.bean` stays.
  > 4. At clean-up, if a temp we are about to remove holds outside bytes, it is renamed to the kept name instead.
  > 5. Recovery stops while a kept file exists for the journal (the name is derived from the journal, so no new state); the blocked screen shows "the file" and "the kept copy"; once the user deletes the kept copy, recovery can resolve.
  > Removed: the swap-back and its sync, the restore's created-file rename-aside-and-back, the second race window. The review-6 temp rule stays as decided: temps then only ever hold our own bytes or a cut write of ours. In that rare collision the file shows Finzytrack's version and the editor's sits beside it as the kept copy. The instant between our last check and the unlink stays a §15 limit. Touches spec §6.4 and §8.1 and ADR-0013's decision line.
- **Why:** the swap-back opened a second window: a second outside save made in it landed in one of our temp names, and a later ordinary try deleted it under the review-6 temp rule (S0061 data-safety red team round 1, H1 and H2; its M1 was the same family). That mechanism had produced findings in three rounds running.
- **Decision line, as amended:** "Then hash the displaced file and compare it with the planned pre-hash" now reads: then check what was displaced; if it is byte-identical to the pre bytes or to our post bytes, it is ours; otherwise it is an outside save, moved at once to its kept name beside the file, never swapped back, and the save stops. "On a mismatch, an outside save landed: its bytes are kept" holds, in the kept copy.
- **Details (agent, S0061):** the kept name is `<name>.kept-<job>`, then `-2` to `-999` when taken, exclusive (`RENAME_EXCL`), a hash standing in for a name past 255 bytes (`ledger_engine.ledger.core.journal.kept_name`); the save refuses before its first effect an entry already standing at one of a file's kept names and a target at another's; a displaced file with a second hard link still stops the save (the hard-link amendment above). Code: `ledger_engine.ledger.handles.displaced` and `keep`.
- **Unchanged:** the swap itself, the hard-link and no-swap amendments above, and the review-6 temp rule (ADR-0012's amendment of 2026-10-27).

## Amendment (2026-10-30): forward pointer (S0061 round 3)

- The line "**Unchanged:** ... the review-6 temp rule (ADR-0012's amendment of 2026-10-27)" in the amendment above no longer holds: the temp rule was refined on 2026-10-29 and replaced on 2026-10-30 (owner: "Staging name (Recommended)"; ADR-0012's amendment of that date). The swap itself is unchanged; what reaches it changed: the new bytes are written under a staging name, synced, renamed exclusively to the temp name and that rename synced before the swap, in the save and in the restore alike.

## References

review:D1 §13.2; review:25 §8.3, §14 item 5; review:27 M1 risks; ADR-0008, ADR-0012; INV-01; CAP-DATA-EXTERNAL-EDIT.
