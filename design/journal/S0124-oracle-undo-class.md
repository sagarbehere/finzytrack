# S0124: a class explains a booked difference only by undoing its own cause (M1b, ADR-0014 amendment of 2026-11-16)

## Brief
- Goal: build the ADR-0014 amendment of 2026-11-16 in the oracle (`scripts/oracle/classify.py`). Since S0123 the oracle compares Beancount's booked result of the old app's write with the intended change booked by Beancount. When the parsed comparison has tagged divergences with classes and the booked result still differs:
  1. take the old app's parsed result;
  2. **undo each tagged class's own cause** on it: for I2, remove the duplicated entries; for every other class, apply the change its eraser names, so the entry reads as intended;
  3. book that again with Beancount's own steps (`semantic_diff.book`, as the reference is booked);
  4. compare it with the intended booked result.
  - Equal: the booked difference is explained by those classes.
  - Not equal: what is left is UNEXPLAINED and blocks, as today.
  No class hides a booked difference by being present. S0123's exception for a FIX-L3-02 lot whose label or date was dropped (same units and costs) becomes a case of this rule: undoing the label drop must give the intended holdings.
- Then run the oracle on the repo's corpora and the demo (copies only, through the harness). Report the verdicts before and after, and update BL-0274: the nasty corpus's I2 cases (coffee and rent held twice, the pad's fill) should be explained if, and only if, removing the duplicates gives the intended booked result. Report each case that stays UNEXPLAINED, with its cause; do not reclassify it away.
- Implements: ADR-0014 and its amendment of 2026-11-16; INV-02; S0123's booked comparison.
- Capabilities: none changes status.
- Invariants touched: INV-02; INV-23, INV-24 (copies only; a T-local report carries counts, classes and case IDs only, no amount, account or lot; `copies.py` unchanged).
- Risk class / lane: Medium / full: tooling whose verdict decides promotion and checks money. Review budget row: **Tooling, gates, measurement**: one verifier round; the fix round fixes only a shape the check's own docstring or must-not-pass list promises to refuse, a false statement of fact, or a finding in a guard of real data or privacy; a High is always fixed.
- The bytes it protects: none; it reports. It must never let a booked difference pass that undoing the tagged classes does not remove.
- Mechanism questions (PROCESS.md §3 step 2): what is it a second opinion on? Whether the old app's result, its known faults undone, means what the intent says, in Beancount's booked terms. The owner chose this over keeping such cases blocking (2026-11-16). Fix-round history: the oracle's classifiers (S0002, S0123: one round each; S0122 reverted).
- Constraints: the legacy app is launched only by the harness, on copies. It never touches the owner's running v0.3 app or the owner's live data folder, and nothing is written into the legacy worktree. Never write that folder's path in any file.
- Acceptance (tests first; each fails on the base commit):
  - **must-pass:** nasty's I2 shape in a small ledger: glob entries duplicated into the root before a `pad`, which is explained once the duplicates are removed; a FIX-L3-02 label drop on a held lot (S0123's exception, now by the rule).
  - **must-not-pass:**
    - an I2 duplicate plus an unrelated money change, where the change is left after the undo;
    - a FIX-L3-02 label drop on a reduction that takes another lot (S0123's A1): undoing the label drop restores the label, so the sale takes the intended lot. If the undo can restore it, it is explained; say which, with the reason;
    - a class tagged on one entry while the booked difference is on another that the undo does not touch.
  - A T-local report carries no amounts.
  - The existing oracle tests stay green, or each change of expectation is listed under Golden changes with its reason.
- Expected doc impact: `classify.py`'s docstring (the rule); the write-path README's oracle line if it states the old rule; BL-0274 updated with the run.
- Open questions: none.
- Base: the commit that records the ADR-0014 amendment (this brief's commit).

## Decisions
- The rule is the owner's (ADR-0014 amendment of 2026-11-16; owner, 2026-11-16, in session: "I accept your recommendations for both questions.", accepting "b. Undo the bug, then compare").

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
