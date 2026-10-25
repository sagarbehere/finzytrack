# Write path: the per-mutator intended-change table

## Orientation
- **What exists:** the table itself (`intent-table.toml` → generated `intent-table.md`) and the differential-oracle harness (`scripts/oracle/`, S0002), whose three models implement the `intent` column of `txn.edit`, `account.rename` and `import.commit` (`model.INTENT_ROWS`). No mutator is built yet; the write contract arrives in M1b (WS-WRITE stage 1).
- **Entry points:** `design/specs/write-path/intent-table.toml` (edit this); `scripts/gen_intent_table.py`; the structural checks in `scripts/tests/test_intent_table.py` (T1).
- **Decisions it applies:** ADR-0008 (contract steps 2 and 3), ADR-0009 (edit primitives), ADR-0014 (clearance kinds, oracle classes), ADR-0026 (metadata list), ADR-0049 (account moves), and the per-area ADRs named in each row.
- **Invariants:** INV-01 (the intent is exactly what step 2 checks), INV-14, INV-15, INV-16, INV-21, INV-22.
- **Traps:** an intent written loosely ("updates the account") lets the contract pass a wrong write; booking errors are never acknowledgeable; `@@` and typed metadata are preserved by merging against the parsed source, never by trusting a request (ADR-0009); a row marked `draft` is not a licence to build, it is confirmed in its vertical's first slice.
- **Main tests:** `scripts/tests/test_intent_table.py` (structure: kinds, primitives, error classes that may be acknowledged, the metadata contract list, known oracle classes and finding IDs, ADRs, capability rows, the legacy map, inbox state); later, each mutator's contract tests cite its row id.

## What a row means

Each `[[mutator]]` row is the contract between a use case and the write-safety contract (ADR-0008):

- **`intent`** is the "targeted set" of step 2: the exact entries inserted, replaced and removed. Every other entry of the ledger set must be identical, and options, plugins and includes are unchanged unless the row's edits include `option_line`. When a mutator is built, its edit producer returns this intended change and step 2 compares it with the candidate. The wording is precise on purpose: "the one targeted `balance` replaced" means a second matching assertion is a refusal, not a guess.
- **`kind`** is the clearance kind (ADR-0014): `add`, `change-one`, `many`. A row takes the **strictest** kind any of its writes can be: one that may write several entries or several files is `many` (an import of 200 lines, a price fetch, an opening with a pad, a new workspace's files), even when a single write is small. A row whose `old` is `n/a` clears at twice its kind's volume and time.
- **`edits`** are the only primitives the producer may emit (ADR-0009): `insert` (routed by Fava's `insert-entry` options, else the default file, never inside a `pushtag` block), `replace` (a whole entry span), `delete`, `replace_token` (exactly one account token on the posting's or directive's own line), `insert_meta` / `delete_meta` (one metadata line; changing a value is a delete plus an insert of that line), `option_line` (one `option` line in the root), `new_file` (a file that did not exist), and `replace_file` for the demo only (the one replacement in the system, INV-25, ADR-0022). A change of flag is a whole-entry `replace`.
- **`files`** bounds where the edits may land; a plan that touches any other file is refused.
- **`meta`** lists the only keys the mutator writes: the ADR-0026 app-written list, or user vocabulary (review:28 §4.2) the user typed. Never client-supplied keys outside the list; every other key already on an entry passes through untouched (INV-21).
- **`ack`** lists the error keys (error class + the entry it is attached to, review:D1 §12.1) the user may be asked to acknowledge. An error not listed refuses the write. Booking errors (`BookingError`, `ReductionError`, `AmbiguousMatchError`) and parser errors are never acknowledgeable.
- **`refuses`** lists what the use case checks before planning, so the user gets a plain reason instead of a contract failure.
- **`oracle`** is the v0.3 expectation when the harness compares the row with the old app: the divergence classes the old app is known to produce (ADR-0014; measured in S0002), or "none: no old endpoint". Every full-rewrite legacy write carries I10, and I2 / I3 on ledgers with glob includes / plugins.
- **`status`**: `decided` when accepted ADRs settle every column; `draft` when the row is the best reading of the ADRs but its vertical has not started (the vertical's first slice confirms or corrects it, and a change to `intent`, `meta` or `ack` there needs the verifier's check against the ADRs); `owner:<inbox file>` when a rubric item 1 question is open.

## Rules

1. A mutator may not be built until its row is `decided`.
2. A new mutator gets a row before its first test. A row is never deleted: a dropped mutator stays with `status` explaining why (as the legacy map's "not built" lines do).
3. A change to a `decided` row's `intent`, `meta` or `ack` is a change to what is written to the ledger: it needs an ADR that covers it, or the owner (rubric item 1).
4. The oracle harness's models follow the table, never the reverse; where a row waits for the owner, the model refuses the case as non-comparable.
