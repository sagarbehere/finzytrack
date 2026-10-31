# Should a save stop computing the "parse digest" for R4, and how do we treat saves that go over 5 s at 54k because of R4?
- Asked: 2026-10-31 by the implementer of S0066-r4 (round 1; verifier r1 F4 and F5)
- blocks: nothing (built as the spec says; the numbers are measured)
- Rubric item: 4 (spec §6.7's words come from ADR-0008's amendment as adopted by ADR-0064) and "a broken user-facing absolute" (over 5 s at 54k entries)
- ADRs checked: ADR-0008 (5 s at 54k, "measured until everything on screen reflects the change", so the publish counts; its parse-cache amendment: build the cache only if the measured save misses 5 s); ADR-0064 (R4 after "saved", gates the next save; its rejected option "R4 inside the save"); ADR-0010 guardrail 3 as amended (R4 always on); spec §6.7 step 4 ("both parse digests equal each other and the record's"). None says whether a save that must first finish the previous save's check counts against 5 s, or whether the record must carry a parse digest.

## Context (plain language, a concrete example first)
You save a change in a 54k-entry ledger. Finzytrack writes it (about 2.3 s today) and says "saved". Right after, in the background, R4 loads the ledger again with Beancount's own loader and checks the result equals Finzytrack's prediction (about 1.7 s). If you save again before that check ended, the second save first waits for the check.

**The parse digest.** To make its prediction, the save computes two fingerprints of its result: the "adopted digest" (the full result, which R4 compares) and the "parse digest" (only the parsed text, before plugins run). The parse digest is used only when R4's two loads disagree: R4 then parses the files again with Beancount's parser and with Finzytrack's own, and requires both to equal each other and the parse digest stored by the save. The verifier argues the stored one adds nothing: R4 already checks that Finzytrack's parser read exactly the bytes the save wrote (it records which bytes it parsed), and that parser always gives the same output for the same bytes, so its result now equals its result at the save. The only way they could differ is a file named another way (a false "check failed"). It costs about 0.5 s of every save at 54k (a fifth of the save). I agree with the verifier.

**The 5 s picture** (measured in S0066 on the demo 54k ledger, one 8.7 MB file, the owner's Mac; an engine start is 0.05-0.24 s):

| A save that ... | today | with the publish (BL-0113, about 1.1 s) |
|---|---|---|
| finds no check owed (the usual case) | about 2.3 s | about 3.4 s |
| first finishes an owed check that passes | about 4.1 s + an engine start | about 5.2-5.5 s |
| first finishes an owed check that needs the confirm (a ledger whose plugins vary run to run) | about 6.5-7 s (two engine starts) | about 7.6-8 s |

The second and third rows happen only when you save again within a few seconds of the last save, or after a restart that left a check owed.

## Options
1. **Keep both as built**: the parse digest stays; the slow rows stay documented in spec §15 ("A save right after a save … can exceed 5 s").
2. **Drop the stored parse digest** (about 0.5 s off every save; no format change needed before release: the save stops filling it, the confirm stops reading it), and **count a save that first finishes the previous save's check as two jobs** for the 5 s target: the check belongs to the earlier save, and the screen says "Checking your last change first…" while it runs. Revisit with BL-0112 at M1b, when the publish exists and the 12-file ledger is measured.
3. Option 2's digest change, but treat the second row as a miss of ADR-0008's 5 s and build the bounded parse cache (ADR-0008's amendment) now. More code before the publish exists to measure.

## Recommendation
Option 2. It makes every save cheaper at no loss of checking, and it states honestly what the target covers. The third row needs a ledger whose plugins vary run to run and two saves within seconds; it is rare and the screen says what Finzytrack is doing.

## Answer (owner, verbatim, with date)
