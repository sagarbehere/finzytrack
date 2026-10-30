# Undoing a save keeps deleting other programs' files at paths the save was creating: stop removing them at all?
- Asked: 2026-10-30 by the orchestrator (S0061 round 5: red team F1 High, verifier F1 Medium and F2 Low; the same mechanism produced red team r2 H1 and r4 F1)
- blocks: S0061-restore (BL-0151 slice 2) fix round, and so slices 3-5
- Rubric item: changes recovery's behaviour and what the user is told and asked (a new blocked-screen case); also ADR-0012 wording an agent amended without you (verifier r5 F4)
- ADRs checked: ADR-0012 and its amendments (2026-10-29 "Refine the rule", 2026-10-30 "Staging name (Recommended)", the agent's round-4 amendment of the created-path proof); ADR-0063 decision 2 (Keep the files as they are now writes nothing to ledger files); ADR-0064 decision 11; spec §8.1 "Afterwards" and §11's kept-copy row (owner 2026-10-29: "Offer to delete it (Recommended)": Delete the kept copy after Show both); INV-32. No ADR decides that undo must delete a created file.

## Context
Sometimes a save creates a new file, for example `y2026/prices.bean`. If that save fails or crashes, undoing it must remove the file again. The trouble: a file standing at that path might be ours, or it might be another program's. A sync client could have restored it, or you could have re-run an import. Another program's file can even hold exactly the bytes we would have written. To tell the two apart we have been adding proofs (our temp names beside the path, kept until the journal is resolved). Each round found a new way around them:
- r2 H1;
- r4 F1;
- r5 F1, the High: the in-app rollback deletes another program's file at a created path when the save failed before it staged that file, then says "not saved, nothing changed". The same happens in recovery after a crash plus a power loss;
- r5 verifier F1: another program's bytes left at a hidden name under a "resolved" journal.

## Options
1. **Never remove a file at a created path (recommended).**
   - When a save is undone and nothing stands at a path it was creating, all is well: our leftovers are removed and the journal resolves, as today.
   - When something does stand there, Finzytrack does not touch it, whatever it holds. It undoes the other files, and the journal stops with a blocked-screen row like this: "Finzytrack was creating y2026/prices.bean when the save was interrupted. It cannot tell whether this file holds that unfinished change or another program's file, so it left it alone. Look at it; if you don't want it, delete it and press Retry, or choose Keep the files as they are now."
   - Buttons: Show it, Delete it (after Show it), Retry, Keep the files as they are now. This is the same flow as the kept copy you approved on 2026-10-29.
   - Removed:
     - the created-path proof (four names, kept until resolved);
     - the rollback's `skip`;
     - the rename aside of a created file and its check;
     - the late clean-up after the resolve;
     - §15's "taken for ours and removed" loss.
   - Cost: you are asked once, after a rare event, namely a failure or crash after the save had already placed the new file. Before that point nothing stands at the path, and the undo stays silent.
2. **Patch again.** The rollback treats every created path it never renamed into as "leave it". Recovery gets a durable mark per created path written before the journal opens, or §15's accepted window is widened. The proof machinery stays, and so does the class of finding.

## Also (batched; my recommendations, facts checked)
- **Read errors (verifier r5 F3, red team r5 F3 and overflow 1, 3).** Any read error during the undo's checks will end the try as `failed` with nothing moved, wherever it happens. Today some of those paths still make a kept copy of our own bytes. The `failed` blocked screen will also name the file and the error, so a permanent cause (a file nobody may read) is visible instead of an endless Retry.
- **The round-4 ADR-0012 amendment** an agent wrote without asking you (verifier r5 F4): with option 1 its accepted-loss line goes away. The rest only records fixes that fail closed. Approve it as amended by your answer here.
- **Not for you (an agent fix, recorded):** red team r5 F2. A folder whose permissions make new files undeletable or unreadable (an inheritable deny ACL entry) will be refused before saving, with the existing `permission` reason. Step 2 will check that each of our own names can be removed.

## Answer (owner, verbatim, with date)
Owner (2026-10-30, in session): to the created-path question, "Never remove it (Recommended)" (option 1 above); to the batched read-error and ADR-0012 round-4 amendment points, "Yes to both (Recommended)".
