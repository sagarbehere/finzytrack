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
- Round 1, head 020070f: **PASS WITH FINDINGS** (no High; risk class High kept). Attacks tried (14): T1 and the generator at head; the legacy `@_serialized_write` set from outside the worktree (26, exact); legacy writes outside LedgerManager; every legacy mutating endpoint against `old`; legacy behaviour behind the inbox claims and FIX tags; the error-class list against the pinned Beancount; every row against ADR-0008/0009/0014/0026/0027/0028/0030/0031/0032/0049/0052 and review:28 §4; 8 mutants against T1 (7 survived); the harness model against `account.rename`; a literal Beancount load of `account.delete-reassign` with A as a pad source; a capability-map sweep for unserved ledger writers; clearance kinds against ADR-0014; oracle classes against S0002; doc impact.
- Findings and resolution:
  - F1 Medium, 13 ledger writers had no row. Added: `txn.delete-many`, `data.undo` (CAP-DATA-UNDO, CAP-TXN-OPLOG), the seven investment quick actions (`inv.buy`, `inv.reinvest`, `inv.dividend`, `inv.sell`, `inv.conversion`, `inv.cd-maturity`, `inv.interest`), `settings.booking-default`, `ws.test-copy`, `ws.adopt-legacy`, `ws.refresh-demo`. The false "only header change" claim in `doc.documents-option` is removed. The table now has 50 rows.
  - F2 Medium, T1 let broken rows through. Fixed: booking and parser errors can never be acknowledged; "as …" must name an existing row; metadata must come from the ADR-0026 list or the review:28 §4.2 vocabulary, and the keys ADR-0026 retired are rejected; oracle entries must be known classes, and every FIX-<id> must be a backlog finding; cut lines must cite an existing ADR; a decided or draft row may not point at the inbox; `INTENT_ROWS` is read as code. All 9 mutants re-run against the new checks are killed. The legacy list stays a committed snapshot (26, checked against the worktree in this slice; the worktree is not in CI).
  - F3 Medium, a pad whose source is A was left dangling. Fixed: merge and delete-with-reassign re-point it to B; delete-with-transactions removes it, and the affected assertions are acknowledgeable and listed.
  - F4 Medium, the rename row and the harness model disagreed on `custom` values. Fixed: both rename account-typed `custom` values and a budget's quoted root; other `custom` string values join the owner's rename question, and the model treats them as non-comparable (new test).
  - F5 Medium, settle and link used primitives ADR-0009 does not have. Fixed: settle is a whole-entry `replace` (the flag changes); appending an `import-key` element is a `delete_meta` plus an `insert_meta` of that line. The README no longer widens `replace_token` to flags.
  - F6 Medium, multi-entry rows were classed `add`. Fixed, as a recorded reading of ADR-0014 that only tightens: a row takes the strictest kind any of its writes can be (README). `import.commit`, `price.write`, `ws.create-ledger`, `ws.seed-demo`, `inv.open-deposit`, `balance.add` and `balance.edit` become `many`; `txn.delete` is split into `txn.delete` and `txn.delete-many`.
  - F7 Medium, ADR-0049 already drops A's own balance and pad on a merge. Fixed: the row follows the ADR, and the inbox question is narrowed to what is really open (A's `open`/`close` and budgets).
  - F8 Medium, edits could remove read-forever identity keys. Fixed: `txn.edit` and `txn.bulk-edit` refuse to remove or change `id`, `transaction_id`, `import-key`, `external_id`, `external_id_type`, `ofx_id` or `source_account` (INV-21 c, INV-14); bulk edit gains the metadata operations CAP-TXN-BULK lists.
  - F9 Low, `import.commit`'s oracle missed the classes S0002 measured. Fixed: it adds FIX-L3-02 and FIX-L3-05.
  - F10 Low, housekeeping. Fixed: `FIX-ADR-0052` removed (ADR-0052 says the v0.3 wizard already writes the currency, so there is no divergence); unused `[defaults]` removed; the rename question covers metadata on every entry and `custom` values; `price.write` refuses to overwrite a manual price with a fetch; legacy `delete_account` with `delete_transactions=false` is mapped as "not built" (ADR-0049).
  - Question for the owner, not a finding: `balance.add` lets you record an assertion that already fails, and `import.commit` lets you "Register anyway" past new failing assertions. ADR-0008 delegates both choices to this table. They go to the owner in the session summary, not the inbox: no rubric item requires it.

## Pilot measurements (S0002–S0004)
- Escalations to the owner: 3 inbox files (INV-24 list, rename subtree and metadata, merge leftovers), all batched, none blocking M0 or M1. One of the three (merge) was partly answered by an existing ADR. The verifier caught that, and the question was narrowed before it reached the owner.
- Defects by finder: the verifiers found 5 High + 15 Medium/Low across the three slices; the gates found 1 (the generated-file check, on the first staging of the intent table); none have been found later so far. Verifier precision: every finding was real (S0002 round 1: 10/10, round 2: 6/6; S0003: 5/5 plus one question; S0004: 10/10). Above the ≥ 70% target.
- Gate times: T0 pre-commit about 1 s; stdlib meta-tests about 0.1 s; oracle unit tests about 0.15 s; check_generated about 0.3 s. End to end (local tier): oracle demo 15 cases about 70 s; S12 runner about 4.5 min.
- Owner time: none spent in these slices; the inbox batch is waiting.
- Ceremony share, from rough agent wall time: S0002 implementation about 80 min; brief, verifier rounds and docs about 25 min plus about 50 min of fixes (fixes counted as implementation): about 0.2. S0004 drafting about 45 min; ceremony about 30 min (two verifier waits about 8 min each, the journal, the generator): about 0.7. Within the ≤ 1.0 target.
- Process lesson: the costly part was not ceremony but rework the verifier forced. Every High was an over-permissive check, and every unit test so far had only asked "does it fire", never "does it fire on what it must not". The one rule worth adding is a negative test beside every positive one for classifiers, guards and table checks. It is proposed for the reflection pass, not applied here.

## Retro
- The verifier's mutation runs (S0002 round 1, S0004) found most of the issues. Seeding a few mutants of one's own before handing a slice over would be cheaper, and a T1 mutation job (PROCESS §8) covers this once the packages exist.
- Orientation gap: the capability map is the inventory of ledger writers, and it should have been swept row by row before the table was written. The table was built from the legacy mutators plus ADRs, which missed the quick actions (they reach the ledger through the legacy commit endpoint).
