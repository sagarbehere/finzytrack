# S0136 round 3 (V3-1): what should a create's cleanup say after a folder removal that answers an error?
- Asked: 2026-11-21 by the orchestrator (S0136's third verifier round, at c86ab233)
- blocks: S0136's landing
- Rubric item: a second fix round on one mechanism (PROCESS.md §3, review budget; the owner's standing rule); a false outcome (class a) in a rare setup
- ADRs checked: ADR-0068 D2; engine-jobs §8.2 (the create's stop and cleanup); `create.Run._cleanup`'s docstring

## Situation
When a create (or a copy, which shares its cleanup) stops, it removes what it made and tells the user what, if anything, it left behind. Round 2 fixed V2-2 (your "Fix it now"): a folder removed and then followed by a failed sync is no longer named kept.

Round 3 found the sibling, V3-1:
- **What happens:** a folder removal that the file system carries out but answers with an error. That is a network share whose reply is lost, or a reconnect that replays the removal. The cleanup takes the error as "not removed" and names the folder kept: "Finzytrack left these in … : ." The folder is gone.
- **Where:** the same at the target folder and at a nested folder.
- **Age:** older than S0136, though round 2's new docstring now claims the opposite ("A name it removed is never named as kept").
- **The verifier's repro:** fails at c86ab233.

Likelihood: rare. It needs a removal that succeeds and still errors, which on a local disk is essentially never, and on a share only around a network drop. No byte is at risk. The error is in the safe direction (it says something stayed when nothing did), and the user is told to look.

Review history of this mechanism, the create's and copy's stop and cleanup: built in S0080 (create) and S0081 (copy); fixed in S0136 round 2 (V2-2). This would be its second fix round in S0136.

## Options (cheapest first)
1. **State it, no code.**
   - Correct the docstring's claim.
   - Add a limit in engine-jobs §8.2: after a removal that answers an error, the cleanup may name a folder kept that is already gone; the text tells the user to check it.
   - A backlog row for a later fix.
   - Cost: none now; S0136 lands. The outcome stays false in that rare case.
2. **Any removal error means "the cleanup could not finish".** On a removal error, report nothing as kept or removed, only "check the folder". The text already exists, `unknown_folder`: "may hold … Check it before you delete anything".
   - Cost: one small change, its test, and a fourth, fresh verifier round.
   - The text is then true in every case. It names less when a removal really failed.
3. **Look again after a removal error** (the verifier's suggestion). On a removal error, check whether the name is still there: if it is gone, treat it as removed; if the check fails, report "check the folder".
   - Cost: a little more code than option 2, and its tests and the fresh round.
   - Names the most, but it is one more step that can itself meet the network drop.

## Recommendation
**Option 2.** It makes the outcome true in every case with the smallest change. It never guesses what a removal that answered an error really did, and it uses a text that already exists. Option 1 is honest if you would rather land S0136 now and fix this with BL-0087, before Windows writes turn on.

## Answer (owner, verbatim, with date)
Owner, 2026-11-21, in session, asked "S0136 round 3 found V3-1: … What should happen?" with the options "Say "check the folder" (Recommended)", "State it, fix later" and "Look again after the error": "Say "check the folder" (Recommended)".

**Decision: option 2.** On any removal error in the create's and copy's cleanup (the target folder, a nested folder or a file), the cleanup reports nothing as kept or removed: `left` is None, told as `unknown_folder` ("check the folder"). The verifier's V3-1 repro becomes its test; a fourth, fresh verifier round follows.
