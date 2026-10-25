# May INV-24's "Enforced by" line list the new copy guards and their tests?
- Asked: 2026-10-25 by S0002 (verifier finding F10)
- blocks: nothing
- Rubric item: 4 (any change to `INVARIANTS.md` is yours)

## Context (plain language, a concrete example first)
INV-24 says agents and tests never touch your real data. Its "Enforced by" line lists the files that check it, and one of them is `scripts/oracle/launch_legacy.py`. In S0002 the checks that refuse your live folder moved into a new file, `scripts/oracle/copies.py`. The harness also gained new checks: it refuses a ledger whose `include` lines point outside the copy, and it stops if the source folder changes. Their tests live in `scripts/tests/test_oracle_copies.py` and `scripts/oracle/tests/test_harness.py`. The rule itself does not change. Only the list of files that enforce it is out of date.

## Options
1. Add `scripts/oracle/copies.py`, `scripts/tests/test_oracle_copies.py` and `scripts/oracle/tests/test_harness.py` to the line, plus an IMPROVEMENTS entry.
2. Leave the line as it is. The launcher still imports the guards, so the existing check stays green.

## Recommendation
Option 1. It keeps "where is this enforced?" accurate, and it adds enforcement without changing the rule.

## Answer (owner, verbatim, with date)
"I accept your recommendations for the four questions in the design/inbox/" (owner, 2026-10-25, in session). Recorded in: `design/INVARIANTS.md` INV-24; IMPROVEMENTS IMP-0007. Option 1 (the recommendation) applies.
