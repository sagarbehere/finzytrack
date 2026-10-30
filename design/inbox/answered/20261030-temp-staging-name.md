# The temp rule keeps losing outside saves: write each temp under its own staging name first?
- Asked: 2026-10-30 by the orchestrator (S0061 round 3: verifier F1 High, F2 Medium, F3 Low; data-safety red team F1, F2 Medium, F3 Low)
- blocks: S0061-restore (BL-0151 slice 2) fix round, and so slices 3-5
- Rubric item: changes the owner's temp rule ("Refine the rule", 2026-10-29) and the save's durable order
- ADRs checked: ADR-0012 amendment of 2026-10-29 (the refined temp rule, the owner's "Refine the rule"); ADR-0064 decision 11 and its amendment (cut temps never block); ADR-0013 and its 2026-10-29 amendments (the kept-name design, "Yes"; every swap hashes what it displaced); INV-32 as amended ("Amend the wording (Recommended)": temporary and kept-copy names derived from the recorded paths). None decides where a temp is first written.

## Context
The temp rule decides when one of our temp files may be deleted. It has drawn findings in every round: r1 M1, r2 M1, M2 and F2, and now r3. The cause is always the same. One temp name plays two roles:
- **Before the swap,** it is where we write the new bytes, so a crash can leave a cut write of ours there.
- **After the swap,** it holds the file's old object. An editor that still has that file open can write into it.

Your refined rule ("empty, a prefix of what we wrote, or equal to the old or new bytes is ours") cannot always tell these two cases apart. The verifier and the red team each showed this, the red team on real APFS:
1. A save appends lines (new = old + more). An editor that had the file open then deletes a few lines in place, writing into the old object at our temp name. Its bytes are a prefix of our new bytes, so recovery deletes them as "our cut write" and resolves.
2. The restore's swap is missing the folder sync that round 2 added to the save's swap. An in-place write plus a power loss leaves the editor's bytes with no name at all. This is the verifier's High; it is a one-line fix whichever option you pick.
3. A real disk can tear an unsynced file with zeros rather than as a prefix. Our own cut temp then becomes a kept copy and blocks recovery. This fails safe, but it breaks "cut temps never block".

## Options
1. **A staging name (recommended).** Write the new bytes first under a separate staging name, `.<name>.ft-<session>-<job>.new`, and sync them. Then rename the staging file to the temp name, sync the folder, and swap. No swap ever touches the staging name, so anything there is our own write and recovery deletes it whatever it holds. A cut never blocks, even a torn one. The temp name then only ever holds our fully synced bytes or the displaced old object. The rule there becomes: equal to the old or new bytes is ours; anything else goes to a kept copy and the journal stops. The restore temp works the same way.
   - Removed: the prefix clause, the "empty" clause, and the post copy `<index>.post-<name>` in the backup folder (it existed only to recognise prefixes).
   - Cost: one extra rename per changed file. The folder sync is already there for the save and is needed anyway for (2). The post-copy write per file goes away.
   - Fixes 1, 2 and 3.
2. **Keep the refined rule and patch.** Add the sync to the restore swap. Accept a prefix only where a cut write of ours can exist (a save temp beside a file at its old bytes, a restore temp beside a file at its new bytes). Add §15 lines for the case left open: a truncating editor writing through a name our swap had not made durable yet. Teach the test fake zero-filled tears. This is less code to change today, but the prefix logic and the post copy stay, and so does the class of finding.
3. **Patch only (2) and list the rest as known limits in §15.**

## Recommendation
Option 1. It removes a mechanism instead of adding a case, it closes all three findings, and it keeps both of your earlier goals: an outside save is never deleted, and a cut temp of ours never blocks. A fresh review round on the fix would follow (data-safety red team included).

## Answer (owner, verbatim, with date)
Owner: "Staging name (Recommended)" (2026-10-30, in session, answering the orchestrator's question with option 1 above).
