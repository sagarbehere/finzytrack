# S0167: M1b simplifications 4 and 5 (BL-0268's prefix folded into `PLAN_UNKNOWN`; metadata lines as a spliced `replace`)

## Brief
- Goal: carry out the owner's answers to proposals 4 and 5 of `design/inbox/answered/20261128-m1b-simplifications.md` in the docs and the spec. No product code.
  - **Proposal 4** (owner, 2026-11-28: "Accept (Recommended)"): "Fold BL-0268's per-run plan-id prefix into the built `PLAN_UNKNOWN`. [...] BL-0268 would keep only the confirm route's own rules (the resent confirm while the workspace warms, engine-jobs §11; the `Host` check, BL-0115), which move to the confirm route's brief. ADR-0008 step 4 gets a dated note."
  - **Proposal 5** (owner, 2026-11-28: "Accept, splice only (Recommended)"; the option as put: "accept, with the rule that the producer splices the line into the entry's own text, so comments and spacing are kept (I10), never re-printed; intent-table rows and ADR-0009 amended; BL-0313 closes without code").
- Implements: ADR-0008 (dated note on step 4), ADR-0009 (amendment of 2026-11-28), spec `design/specs/write-path/` (intent table and README), ARCHITECTURE.md §9.2.
- Capabilities: none change status (CAP-ACCT-EDIT, CAP-ACCT-RENAME and the later rows keep their milestones; only their rows' `edits` change).
- Invariants touched: INV-01 (the intent is exactly what step 2 checks), INV-19 (docs agree), INV-21 (other keys untouched).
- Kind: docs and spec; the only code is the intent table's structural check (`scripts/tests/test_intent_table.py`, T1), whose primitive list loses the two dropped names, and the generated `intent-table.md`. Review row: docs only (verifier on facts and doc consistency).
- Risk class / lane: Low / light.
- The bytes it protects: none directly (no mechanism is added); the rule it writes down protects comments and spacing inside a metadata-edited entry (I10) once the M4 producers are built.
- Acceptance tests: `scripts/tests/test_intent_table.py` (the primitive check refuses `insert_meta` and `delete_meta`; it fails on the base commit, where eight rows name them).
- Expected doc impact: ADR-0008 note, ADR-0009 amendment, intent table rows + generated view, write-path README, ARCHITECTURE.md §9.2 step 2, backlog BL-0268 (narrowed) and BL-0313 (closed), the G1 refusal tool's docstring (it cites BL-0313).
- Open questions: none for the inbox. Two observations recorded under Decisions (D4, D5).

## Decisions
- D1 (agent): ADR-0008's step 4 is the concurrency check (the fingerprint), not the confirm; the note's "ADR-0008 step 4" reads as the step the confirm runs (ARCHITECTURE.md §9.2 step 4). No ADR decided the per-run prefix (it was BL-0268's own idea, S0117 round 2 RT2-F2). The note is written on ADR-0008 as the owner asked, and says what it narrows: a plan is confirmed only in the run that made it; any other id is `PLAN_UNKNOWN`; there is no per-run prefix.
- D2 (agent): BL-0268 keeps the confirm route's own rules (the resent confirm while the workspace warms, the `Host`/`Origin` check of BL-0115, G1 lens L2-F4's next steps, the poll under the WARMING refusal), so the row stays open on M3: it is the confirm route's brief. Only the prefix paragraph goes. The code comments citing BL-0268 for `PLAN_UNKNOWN` (`app.platform.plans`, the error registry) stay true and are not touched.
- D3 (agent, inside the owner's decision): changing one metadata line's value was a delete plus an insert of that line; under the amendment it is the same `replace`: the entry with that line's bytes changed, spliced. All metadata lines changed on one entry are one `replace` (two edits on one entry overlap and the plan aborts, ADR-0009; `source_edit` refuses `overlapping_edits`), and a `replace_token` on that same entry (a rename whose `open` also carries a metadata value naming the account) is spliced into that same `replace`.
- D4 (agent, a stated limit in the amendment): the contract cannot see whether the splice kept a comment inside the replaced entry: step 2 compares parsed entries and step 2b compares only the bytes outside the spans. The rule is the producer's, pinned by each producer's tests (a comment and odd spacing inside the entry survive); whether step 2b also checks inside such a span (only whole metadata lines differ) is left to `account.edit`'s brief (M4), its first producer.
- D5 (agent, observed, not changed): `meta.cleanup` (decided, M10) keeps `replace_token` for "snake_case vocabulary renamed", but `replace_token` holds an account token only (README; `ReplaceToken.old` is an `AccountName`), so a key rename is now a spliced `replace` too. Changing a decided row's `edits` beyond what the owner accepted is rubric item 1, so the row keeps `replace_token` beside its new `replace`; reported to the orchestrator for M10's brief.

- D6 (agent): "carrying the splicing rule": the README's `edits` definition states the rule in full, and each of the 8 rows' `intent` names it in one clause citing the amendment, so a producer reading only its row meets it. The `intent` of the two `decided` rows that change wording (`account.edit`, `import.link-element`) changes how the write is produced, not what step 2 accepts; rule 3 of the README is met by the owner's answer and the ADR-0009 amendment.

## Population: every mention of `insert_meta` / `delete_meta`
Inventory: `grep -rn "insert_meta\|delete_meta"` over `design/`, `engine/`, `backend/`, `scripts/`, `frontend/src`, `.claude` at the base commit (226adf85), plus `BL-0313` (the row that tracks them) and `InsertMeta`/`DeleteMeta` (no code class exists: none found).
- Intent table source (`intent-table.toml`): 9 lines (the header comment, 8 rows: `account.edit`, `account.rename`, `doc.txn-attach`, `import.link-element`, `recon.proceed`, `import.bank-id-migration`, `inv.commodity-metadata`, `meta.cleanup`): all changed.
- Generated `intent-table.md`: 16 lines (8 summary rows, 8 detail lines): regenerated.
- `design/specs/write-path/README.md`: 1 (the `edits` definition): rewritten; the Orientation line "the rest are not built (BL-0312, BL-0313)" also changed.
- `design/ARCHITECTURE.md`: 1 (§9.2 step 2): changed.
- `design/backlog.md`: 1 (BL-0313): rewritten to close without code; `in-progress` in the hand-off commit, `done` when the slice lands.
- `scripts/tests/test_intent_table.py`: 1 (`EDITS`): the two names removed.
- `design/inbox/answered/20261128-m1b-simplifications.md`: 1: the owner's answered note, history, untouched.
- `design/journal/` (S0004, S0115, S0162, M1b-exit): history, untouched.
- `BL-0313` cited without the primitive names (outside the backlog, journals and inbox): 2 (`write-path/README.md` Orientation line, `scripts/oracle/g1_refusals.py` docstring): both rewritten.
- Counts at the base, outside the journals: 30 lines naming the primitives + 2 lines citing only BL-0313 = 32; 31 changed or regenerated, 1 kept (the answered inbox note). After: the names appear only in the ADR-0009 amendment and the T1 check's comment (both on purpose), the inbox note and the journals.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)

- Checks that accept, tag or refuse, each with its must-not-pass test: the intent table's primitive check (`test_kinds_edits_and_milestones`, `EDITS` in `scripts/tests/test_intent_table.py`) now refuses `insert_meta` and `delete_meta`. Must-not-pass: the base commit's table (226adf85), which names them in 8 rows: run with the new `EDITS` against it, 8 subtests fail (`account.edit`, `account.rename`, `doc.txn-attach`, `import.link-element`, `recon.proceed`, `import.bank-id-migration`, `inv.commodity-metadata`, `meta.cleanup`); after the table change, 17 tests OK.
- Self-mutation: skipped (tooling; the check guards the spec, not ledger data). The one loosening that matters, putting the two names back into `EDITS`, is the base commit's own state, shown above to let the 8 rows through.
- Population: see "Population" above (grep over design/, engine/, backend/, scripts/, frontend/src, .claude; counts in and out there).
- Gates: `check_invariants.py`, `check_capabilities.py`, `check_generated.py` exit 0; `run_meta_tests.py` OK, 1032 tests in 41 modules.

## Verifier
One round (docs row). Head verified: f114324b. Verdict: PASS WITH FINDINGS. Checks: every owner quote verbatim; D3 implied by the decision (`_overlap` refuses a `Replace` and a `ReplaceToken` on one entry, so folding is forced); D5 conservative; the 8 rows and the regenerated view (byte-identical); the test fails on the base table (8 subtests); no live mention left; the D4 limit true against `semantic_diff._file_diff` and `outside_spans`; the ADR-0008 note's facts (`PLAN_UNKNOWN`, `PlanPolicy.max_used`, `fate`); BL-0268's remainder real and on M3; BL-0313 closable.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| V1 | Low | d | Fixed (the coordinator): `account.rename`'s intent no longer extends the splicing rule to a `custom` line (the owner's decision covers metadata lines only) |
| V2 | Low | d | Fixed (the coordinator): ADR-0009's limit and the write-path README now say the contract also cannot see a metadata value's quoting or the order of tags and links inside a spliced span; each producer's tests pin all four |
| Observation | — | d | Fixed: ADR-0008's Status line now names the G1 amendment of 2026-11-28 too |

## Landing
BL-0313 done.

## Retro
- The note the coordinator wrote cited "ADR-0008 step 4" for the confirm; the step is ARCHITECTURE.md §9.2's (D1). Cite the spec step, not a guess.

