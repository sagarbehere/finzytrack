# Fourth round on the confined save: fix one missing error class now, or land and track it?
- Asked: 2026-11-14 by the orchestrator (S0117, fourth fix round on one mechanism: ADR-0068 D6)
- blocks: S0117-contract-confirm
- Rubric item: a further fix round on one mechanism needs the owner's options note
- ADRs checked: ADR-0014; the owner's decisions of 2026-11-14 on S0117 ("Go with (2), fix it now"; "3"; "2", `20261114-test-copy-no-links.md`).

## Situation (plain language)
Your option 2 (a test copy's save follows no links) held: the red team's fourth run made about 17,000 attempts, including 4-minute end-to-end runs with a program flipping folders, and never got your real ledger written, nor a false "saved" or "unchanged". The verifier agrees: no path left sends bytes outside the copy.

Both found one slip in the new code: the new "a link was found" error is caught by only one of the writer's three error handlers. In a race inside the test copy (another program puts a link or a file where the save is about to make a folder, in the instant after it checks), the save stops with an internal error instead of its normal answer. In one variant, past the point of no return, it skips the immediate rollback: the copy's file stays changed and the save's record stays open, the app truthfully says saving is paused and offers Reopen, and Reopen's recovery restores the file. Nothing outside the copy is touched. In the other variant the refusal's reason is wrong ("the engine stopped").

## Likelihood
Not reachable until M3 (no test copies exist yet), and then only if another program makes a link or a file at exactly the name of a folder the save is creating, within microseconds.

## Review history of this mechanism
S0117 rounds 1-3: three Highs in the link family (a file link; a folder link; a folder flipped between two looks), closed by the engine's folder check, the recorded identity (your "3") and the no-links walk (your "2"). Round 4: no High; this slip.

## Options (cheapest first)
1. **Land S0117 now and track the slip as a backlog row** (fixed with the M3 copy job, when test copies first exist). Cheapest; the slip stays in code until then.
2. **Fix it now** (recommended): the missing error class joins the two other handlers, so the race rolls back at once and gives its true reason; a test for each variant; plus the round's small test and wording gaps (a test for the new spelling rule, stale comments, a misplaced docstring, the "is a link" wording for a file). Then one verifier round on that fix. No red team: it is no new High and no new mechanism.

## Also for your awareness (no action needed)
Your option 2 said "nothing is ever looked up again by path". As built, that holds for the save's own steps; the rollback of a failed confined save, and recovery after a crash, still open the copy's folders by path, but each one through the path guard (which refuses any link) and with the folder's recorded identity checked. Neither reviewer found a way to steer them; to do so, someone would have to move your real folder to the copy's exact path and forge its creation time, which is the limit already recorded. Making them walk too would be a further change; I do not recommend it now.

## Recommendation
Option 2: it is a two-place omission with an obvious fix, cheaper to close now than to track, and it makes the "rolls back at once" rule true again.

## Answer (owner, verbatim, with date)
Owner, 2026-11-14, in session: "2"
