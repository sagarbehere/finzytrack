# A number written with more than 28 significant digits: keep it exact, or follow Beancount's rounding?
- Asked: 2026-10-28 by the orchestrator, from S0050's fix round (BL-0123) and its verifier round 2 (R2-F1)
- blocks: BL-0123, and the mirror publish (BL-0113) for ledgers that hold such numbers; not S0050
- Rubric item: 3 (finance correctness: which number the user sees)
- ADRs checked: ADR-0010 (overlay load: Beancount's own booking and validation), INV-02 (Beancount's semantics are not reimplemented), INV-22 (numbers fail closed), the mirror README "Exact numbers" (the mirror never rounds; a result that would need rounding fails the build), ADR-0059 (load refusals). None says what happens when Beancount itself rounds a number the user wrote.

## Context (plain language, an example first)
Crypto tokens often use 18 decimal places. A user writes `12345678901.123456789012345678 SHIB` (29 significant digits) and lets Beancount fill in the other leg. Python's default decimal precision is 28 digits, and Beancount's parser and booking use it, so:
- the filled-in leg comes out as `-12345678901.12345678901234568` (the last digits rounded), silently; balances carry the rounded value; only a later balance assertion notices (off by 2E-18);
- in one shape (a leg left blank so Beancount must compute it from a 29-digit amount) our load crashes instead of returning a result (a bug either way; it gets fixed whatever you choose).
`bean-check` and Fava round the same way, so today every Beancount tool agrees on the rounded value.

## Options
1. **Exact, as written.** The engine loads with 64-digit precision (the mirror's precision), so every number is exactly what the ledger says. Finzytrack then differs from `bean-check` and Fava in those digits; the load adds a warning naming each line where they would differ, so the user knows why.
2. **Follow Beancount.** Keep Beancount's rounding, so Finzytrack, `bean-check` and Fava agree; the load adds a warning naming each line whose number Beancount rounded, and the crash is fixed (the load returns a refusal or an error row, never raises).
3. **Refuse.** A ledger with a number Beancount would round does not load; the message names the line. Fail closed, but the user cannot use Finzytrack with that ledger until they shorten the number.

## Recommendation
Option 1. The mirror's whole rule is exact numbers, and the number in question is one the user wrote, not one Beancount computed. The difference from other tools is in the 28th digit and is named by a warning, so nothing is hidden. It does not reimplement Beancount (INV-02): Beancount's own booking runs, only with more precision. If you prefer that every Beancount tool shows the same figure, option 2 is the honest alternative.

## Answer (owner, verbatim, with date)
