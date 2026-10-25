# Do you approve the pilot's process changes (ADR-0057)?
- Asked: 2026-10-25 by S0005-pilot-adr
- blocks: nothing (the changes are tightenings, already applied as PROCESS.md §9 allows; if you reject one, it is reverted)
- Rubric item: "Always the owner's: approving changes to PROCESS.md (agents may apply tightenings first)"
- ADRs checked: ADR-0046 asks for this ADR; ADR-0001 and PROCESS.md §9 say PROCESS.md changes need your approval.

## Context (plain language, a concrete example first)
In the last three slices the verifier found five serious bugs, and all five were the same kind: a check that let through something it should have refused. Example: the oracle's "price rounding is expected" rule was applied to every priced line with a fixed one-cent margin, so it could hide a real price change, not only the rounding the old app does. The tests only asked "does the rule fire on a good case", never "does it stay quiet on a bad case". The verifier found most of these by deliberately breaking the code (a "mutant") and seeing that no test noticed.

## Options
1. Approve all four PROCESS.md changes (ADR-0057 items 1–4): before hand-off the implementer (a) lists each accept/refuse check with its must-not-pass test, (b) breaks each such check on purpose with the new `scripts/mutants.py` and records how many breaks the tests caught, (c) builds "every X" lists by behaviour from the capability map, not by name; and (d) a second verifier round runs after fixing a serious finding or three or more medium ones.
2. Approve some of them (say which).
3. Reject them (they are reverted).

Nothing here loosens a gate. The escalation rubric's items are unchanged. Item 5 (the inbox "ADRs checked" line) only changes a template, so it needs no approval.

## Recommendation
Option 1. Cost: a few minutes per check before hand-off. It already paid for itself on this slice: 3 of the first 8 deliberate breaks of the new tool went unnoticed by its tests, which showed two missing tests and one piece of dead code before the verifier saw it.

The ADR also gives the first rough slice count you asked for at the pilot (ADR-0046): about 180–270 slices for M1–M12, very roughly 550–1,350 agent hours. This is an order of magnitude only; the G1 evidence pack replaces it with the pace measured through M1b.

## Answer (owner, verbatim, with date)
"I'll go with your recommendation for the PROCESS.md changes" (owner, 2026-10-25, in session). Option 1: all four changes approved; recorded in IMPROVEMENTS IMP-0010 to IMP-0012 and in ADR-0057's status line.
