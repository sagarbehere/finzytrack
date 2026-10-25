# S0004: The per-mutator intended-change table (the process pilot)

## Brief
- Goal: write the M1 spec that says, for every ledger-writing mutator in the v0.4 scope, what it is allowed to change: its kind of edit (for clearance), the edits it produces, the exact intended change the contract's step 2 checks, the metadata it writes, the error keys it may ask the user to acknowledge (contract step 3), the old endpoint or n/a, and the v0.3 oracle expectation (the divergence classes measured by the S0002 harness). This is the process pilot (owner, 2026-10-24, review:F §2 item 5; ADR-0046): it runs under the full process and is measured with the pilot metrics below.
- Implements: ADR-0008 (step 2 and the error identity of step 3), ADR-0009 (edit producers), ADR-0014 (promotion, clearance kinds, oracle classes), ADR-0026 (the metadata list), ADR-0027, ADR-0028, ADR-0030, ADR-0031, ADR-0032, ADR-0049, ADR-0052; review:D1 §5, §12.1, §12.6, §12.8; review:28 §4.1; review:27 §1(d) M0 "M1's specs only".
- Capabilities: none built; the spec cites the CAP rows each mutator serves.
- Invariants touched: INV-01 (the intent is what step 2 checks), INV-14, INV-15 (no proposed account names), INV-16, INV-21 (keys read forever, every other key passed through), INV-22.
- Risk class / lane: High / full (it defines what may be written to the ledger; rubric item 1 for any row no ADR covers).
- Acceptance tests: the spec is data plus prose, so its checks are structural (`scripts/tests/test_intent_table.py`, stdlib): every row has every column; every kind of edit is one of ADR-0014's four; every acknowledgeable error key names a Beancount 3.2.0 error class; every CAP id exists in `design/capabilities.toml`; every legacy mutator decorated with `@_serialized_write` in the legacy worktree maps to a row or to an explicit "not built" line with its ADR; the three S0002 mutators' rows agree with `scripts/oracle/model.py` (the harness models defer to the table).
- Expected doc impact: new `design/specs/write-path/intent-table.md` (+ its machine-readable `intent-table.toml`); `design/specs/README.md` (the area exists); backlog WS-QA item 10 status; this journal with the pilot metrics.
- Open questions: rows that no accepted ADR settles go to the inbox, batched, with a recommendation (rubric item 1).
- Base SHA: e4d1f90.

## Pilot metrics (PROCESS.md §10 as adopted; review:C §5 S10 pass criteria)
Measured across this slice and the slices around it (S0002, S0003, S0004):
- escalations reaching the owner (count, and whether each needed the owner);
- defects found by the verifier vs by the gates vs later;
- gate times per tier;
- doc-check hits (the verifier's doc check; there is no separate doc-auditor yet);
- owner time spent;
- ceremony share (agent time in brief, verify, docs, land ÷ implementation time), target ≤ 1.0 on Medium slices;
- verifier finding precision (real findings ÷ findings), target ≥ 70%.

## Decisions
- Source of truth is TOML (stdlib-readable), rendered to markdown by `scripts/gen_intent_table.py` and checked by `check_generated.py`, as the capability map is (agent).
- Scope: every ledger-writing mutator in the v0.4 scope, 37 rows, from the 26 legacy mutators (each mapped; statement import and its undo are "not built", ADR-0030) plus the v0.4-only ones (merge, delete with reassign, pending settle, import-key linking, reconciliation, bank-id migration, booking-method change, one-upload sync, metadata cleanup, demo seeding). AI proposals add no rows: they commit through the same use cases (ADR-0033) (agent).
- Rows the accepted ADRs settle are `decided`; rows of later verticals whose details no ADR fixes are `draft` and are confirmed in their vertical's first slice (README rule 1: nothing is built from a draft row) (agent).
- The acknowledgeable error keys are decided here, as ADR-0008 delegates them to this table; booking and parser errors are never acknowledgeable (agent).
- Two rows hit rubric item 1 and wait for the owner, batched with a recommendation: `account.rename` (subaccounts and metadata values naming the account) and `account.merge` (A's own balance checks after a merge). Neither blocks M0 or M1 (agent).
- The S0002 harness models now name their rows (`model.INTENT_ROWS`), and a T1 check keeps both sides in step (agent).

## Verifier
- Head SHA verified:
- Attacks tried:
- Findings:

## Retro
-
