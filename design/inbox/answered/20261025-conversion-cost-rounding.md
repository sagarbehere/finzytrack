# When a fund converts at a ratio whose cost doesn't divide exactly, how should the conversion be recorded?
- Asked: 2026-10-25 by S0004 (the intent table, row `inv.conversion`; found by the verifier)
- blocks: the `inv.conversion` row (the Merger quick action, built in M8). Nothing before M8.
- Rubric item: 2 (finance semantics: what a number means); ADR-0030 and ADR-0031 do not say.

## Context (plain language, a concrete example first)
You hold 100 units of fund X, bought for 1,000 INR in total (10 INR each). X does a 1-for-3 bonus or face-value change, and now you hold 300 units of Y. Nothing was bought or sold: the total cost stays 1,000 INR, so each new unit cost 3.333… INR.

Beancount cannot write 3.333… exactly. Whichever way the new lot is written (per unit, as a total `{{1000 INR}}`, or as `{# 1000 INR}`), Beancount 3.2.0 reports "Transaction does not balance" by a tiny fraction of a rupee. Because the costs are whole rupees, there is no rounding allowance to absorb the difference. Today's draft row would simply refuse these conversions, and they are common for Indian mutual funds.

## Options
1. **Add a small rounding leg** to a "rounding" account you pick (for example `Equity:Rounding`). The transaction balances exactly, the lot cost is written to a few decimals, and the preview shows the leftover (here −0.000…1 INR).
2. **Set a cost tolerance for INR** with Beancount's `inferred_tolerance_default` option, written once into the ledger with your consent. The tiny difference is then accepted everywhere, which affects every transaction in INR, not just conversions.
3. **Refuse such conversions** and explain why. You record them by hand.

## Recommendation
Option 1. It keeps the books exact and visible, changes nothing else in the ledger, and is how practitioners usually handle it in Beancount. Option 2 is quieter but loosens checking for every INR transaction, which goes against "safety outranks every number". The account is one you pick, never one Finzytrack invents (INV-15).

## Answer (owner, verbatim, with date)
"I accept your recommendations for the four questions in the design/inbox/" (owner, 2026-10-25, in session). Recorded in: ADR-0030 amendment; intent table row `inv.conversion`. Option 1 (the recommendation) applies.
