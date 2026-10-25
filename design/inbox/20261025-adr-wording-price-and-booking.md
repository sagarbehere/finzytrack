# Two ADR sentences describe Beancount less exactly than Beancount behaves: may I correct their wording?
- Asked: 2026-10-25 by orchestrator/S0024-beancount-vectors (verifier findings F1 and F6)
- blocks: nothing (the vectors already pin Beancount's real behaviour and the specs point to them; only the ADR text is loose)
- Rubric item: 4 (changing an ADR's text)
- ADRs checked: ADR-0024 (decides that Beancount's price map is the oracle; its parenthetical "same-day duplicates resolved (last by id)" is the loose part); ADR-0031 (decides that every booking method Beancount books is supported; its table row "STRICT (default), STRICT_WITH_SIZE: requires the lots to be named" is the loose part). Neither decision changes; only the description of Beancount does.

## Context (plain language, a concrete example first)

1. **Two prices on the same day, in both directions.** A ledger has `2020-01-02 price USD 76 INR` and, also on 2020-01-02, `price INR 0.0125 USD` (which means 1 USD = 80 INR). Beancount answers "USD in INR on 2020-01-02" with **80**: when both directions have a price on the same day, the one written in the other direction wins, wherever it sits in the file. ADR-0024 says same-day duplicates are resolved "last by id", which would give 76 if the 76 line came last. Our test vectors pin 80 (Beancount's answer), so code built from them is right; the ADR's sentence would mislead someone reading only the ADR.
2. **STRICT booking without naming a lot.** ADR-0031's table says STRICT and STRICT_WITH_SIZE "require the lots to be named". In fact Beancount books a sale written with `{}` under STRICT when only one lot matches, and under STRICT_WITH_SIZE when exactly one lot has that size. The vectors pin this; the M8 lot picker will start from them.

## Options
1. **Correct the two sentences** (recommended): ADR-0024's parenthetical becomes "same-day duplicates resolved as Beancount's price map does (the vectors in `corpora/vectors/` pin the rule)", and ADR-0031's row says "STRICT: a reduction must match one lot unambiguously (`{}` books when only one lot matches); STRICT_WITH_SIZE: as STRICT, and an exact-size lot wins". Recorded as amendments with your words quoted.
2. **Leave the ADRs as they are**; the specs and vectors say what Beancount does.

## Recommendation
Option 1: both ADRs already say "do what Beancount does"; the fix only makes their descriptions match it, so nobody builds from the wrong sentence.

## Answer (owner, verbatim, with date)
