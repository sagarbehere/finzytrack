# Should R4 pause saving when a plugin makes Beancount's result differ from Finzytrack's prediction the same way every time?
- Asked: 2026-10-31 by the implementer of S0066-r4 (round 1; red team r1 F3)
- blocks: nothing (built as your answer 4 of 2026-10-26 says: a notice, no pause)
- Rubric item: 4 (it would change an accepted owner decision, spec §6.7 step 4 and §15)
- ADRs checked: ADR-0008 and ADR-0010 amendments (R4, its outcomes); owner answer 4 on review 2 (2026-10-26, accepted recommendation: "non-deterministic plugins do not block saving (only a real loader difference pauses; plugin differences show a notice)"), which spec §15 cites. No ADR treats a plugin difference that repeats.

## Context (plain language, a concrete example first)
Example: a plugin that reads your main ledger file from disk itself (say, to add a note with the file's hash). When Finzytrack predicts the result of a save, the file on disk still has the old text, so the plugin sees the old text. When R4 checks after the save, the plugin sees the new text. The results differ on every save, though nothing is random. Other causes: a plugin's source edited between a save and its check; a plugin reading a file Finzytrack does not track (BL-0124).

Today R4 sees that both parsers read the same text the same way, so the difference comes from a plugin, and it says "Your ledger's plugins give a different result each time they run." Saving goes on. That is your answer 4. The red team says this lets a wrong prediction through on every save. The orchestrator asked for a fix that runs Beancount's load a second time and pauses saving when it gives the same other result again. I did not build that pause, because it would reverse your answer; I built and tested it in round 1, then removed it (about 40 lines and a protocol field; it adds about 1.7 s to that check at 54k).

What a wrong prediction costs: the views show the predicted result until R4 publishes Beancount's real result. R4 publishes after this outcome once the mirror publish is built (BL-0113); until then the views stay on the prediction until the next rebuild. Your ledger file is never affected.

## Options
1. **Keep your rule** (a notice, no pause). Once R4 publishes (BL-0113), what you see is corrected right after each check.
2. **Pause on a difference that repeats** (the second load): "check failed", Resume saving each time. For someone with such a plugin, every save then pauses.
3. **A clearer notice without a pause**: the second load tells "varies each run" from "your plugins read something Finzytrack does not track", and the notice says which. Costs the second load on that path.

## Recommendation
Option 1. The ledger is safe either way; option 2 makes a rare setup unusable without fixing anything for it, and the views are corrected once R4 publishes.

## Answer (owner, verbatim, with date)
2026-10-31, in session, choosing the option labelled "Keep your rule (Recommended)". (Orchestrator's note, from S0066 verifier r2 F1, found after the question was put: one cause was missing from the list above: Finzytrack's own prediction of Beancount's built-in documents step, which a file-creating save into a documents root makes differ with no plugin at all. That is a Finzytrack defect, not a plugin behaviour, so the owner's rule does not cover it: BL-0172.)
