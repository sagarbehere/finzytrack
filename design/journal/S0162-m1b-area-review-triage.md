# S0162: the M1b area review's triage

## Brief
- Goal: dispose of every finding of the M1b area review (fresh reviewer, 2026-11-28, head 78f105fa; no High, nothing in class (a) or (b)): fix stale docs now where cheaper than a row, add backlog rows on the milestone of each one's first consumer, record the review in `design/journal/M1b-exit.md`, and put its five simplification proposals to the owner in one inbox note, implementing none.
- Implements: PROCESS.md §4 (triage of altitude-2 findings), IMP-0070 (milestone of the first consumer), IMP-0075 (a Low (c)/(d) is fixed or dropped, never a row: F6, F7 fixed; F8 is a restructure, not a wording fix, so a row), IMP-0079 (review row).
- Capabilities: none changed (CAP-DATA-ENGINE-JOBS, CAP-DATA-WRITE-GATE: docs only).
- Invariants touched: none. INV-19 (no generated file touched), INV-23 (IDs and counts only).
- Kind: docs and backlog only, plus one meta-test (`scripts/tests/test_engine_jobs_limits.py`, run by `run_meta_tests.py` at every push) and one docstring in product code (`app.platform.plans`, no behaviour). Review row: **docs only**, one verifier round on facts and doc consistency.
- Risk class / lane: Low / light.
- The bytes it protects: none (no mechanism added or changed).
- Acceptance tests: `scripts/tests/test_engine_jobs_limits.py::LimitsIndex::test_the_repository_spec_index_matches_its_body` fails on the base df738f0e (17 problems: 16 limits missing from §15, "request size" stated nowhere); its four sample tests are the check's must-not-pass cases.
- Expected doc impact: `design/specs/write-path/README.md` (Orientation, `intent`, `edits`); `design/ARCHITECTURE.md` §2.3, §6.1, §9.2 steps 1, 2, 4, 4.10, the TransactionId type row, §12; `design/specs/engine-jobs.md` Orientation, §8.1 "Afterwards", §11 (a rule after the table), §15; `design/backlog.md` (BL-0312 to BL-0318 new; BL-0059, BL-0154, BL-0256, BL-0257, BL-0268 edited; no milestone changes); `backend/src/app/platform/plans.py` docstring; `design/journal/M1b-exit.md`; `design/inbox/20261128-m1b-simplifications.md`. No generated doc, no user doc.
- Open questions: `design/inbox/20261128-m1b-simplifications.md` (blocks nothing).

## Decisions
- **F1 split into two rows, not one on M3** (agent; pushes back on the brief's "on M3"): by IMP-0070 a row sits on its first consumer's milestone. `option_line` (`doc.documents-option`), `replace_file` (`ws.refresh-demo`) and `new_file` (`ws.create-ledger`) first appear in M3: BL-0312. `insert_meta` and `delete_meta` first appear in M4's `account.edit`: BL-0313 on M4. The brief's sixteen mutators are counted from `intent-table.toml` (the union of the rows naming any of the five primitives: 16).
- **BL-0312 asks whether `new_file` belongs in the edit primitives at all** (agent): the create and copy jobs (S0080, S0081) already write new files outside the edit plan, so `ws.create-ledger`, `ws.seed-demo`, `ws.test-copy` may name the job instead; that changes the intent table, so it is decided in that row's brief (rubric item 1 unless an ADR covers it), not here.
- **F2's TransactionId map goes on BL-0059 (M5), not a new row** (agent; the brief allowed either): BL-0059 is the resolver for the same mutators, and the map is ADR-0026's. The row adds one constraint found while checking: the save answers before its rebuild (§9.2 step 5), so the map is computed from the plan's texts.
- **F2's ARCHITECTURE rewrite states what is built and leaves the resolver's place open** (agent): where BL-0059's `(TransactionId, entry_hash)` lookup runs (sidecar before the preview, or the producer in the engine) is not decided anywhere; the text says so instead of inventing it.
- **F4 is a duplicate of BL-0257** (agent; pushes back on adding a new open decision to BL-0154): BL-0257 (M2, M1a area review F3) already names `blocking_for`'s missing `ledger_keys` source and says "Settle before the switch". Pointers on BL-0257 and BL-0154 and the §8.1 open question cite it; no new decision text that could drift from it.
- **F7: "request size" removed, not repointed** (agent): no section states a request-size limit since S0120 made the line a 256 MiB guard (§5.5's rule, "a write meets no size limit of its own"). The check is about 25 lines of logic, under the brief's ~30, so it is built, not a row. It is a docs-consistency meta-test, not gate self-defence (PROCESS.md §9's freeze), and the drift it catches is a real escape (16 limits).
- **BL-0256's note names M4, with M3 mentioned** (agent): the brief said M4's brief; M3's `doc.documents-option` is the first mutator through the preview, but a one-off, while M4's account verticals are the first a user saves twice in quick succession. The note says both.
- **F3: `app.platform.plans`'s module docstring corrected in the same slice** (agent): it still said an unknown id "reads as `PLAN_EXPIRED`", the same staleness as BL-0268's lead.
- **Overflow beyond the two named items** (agent): the orchestrator's message said "drop the others with a reason" without listing them; their IDs are not in this repository's record. Recorded as such in `M1b-exit.md`, and asked of the orchestrator in the hand-off.
- **Simplifications** (agent): one inbox note, cheapest first per proposal, with each mechanism's review history. Recommendation: accept 1, 4, 5; 3 is BL-0314 already; keep 2 for now (recent fix rounds; revisit with CAP-WS-TEST-COPY). The brief named proposal 2's error wrongly in passing; checked against `promotion_refusal`: today's early refusal is `UNPROMOTED_MUTATOR_ON_REAL_LEDGER`.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test: `test_engine_jobs_limits.problems` → `test_a_limit_missing_from_the_index_fails`, `test_an_index_entry_no_section_states_fails`, `test_an_entry_naming_the_wrong_section_fails` (and the repository test, red on the base). Stated limits in its docstring: topics matched by words ignoring case; only the bold `**Limit (topic):**` form is seen; §15 and §16 are not body.
- Self-mutation: not run (tooling, docs consistency; it guards no ledger data, finance result, privacy or security; optional per ADR-0057).
- Population: the §15 index against every `**Limit (…)**` marker in §1-§14 (101 in the body after the slice, 101 in the index; before: 101 body topics, 86 index entries, 16 missing, 1 stated nowhere). The sixteen mutators of F1 from `intent-table.toml`: 16 rows name one of the five unbuilt primitives (`new_file` 4, `replace_file` 1, `option_line` 4, `insert_meta` 8, `delete_meta` 6, overlapping).

## Verifier
- Round 1, head a1a16db3 (range df738f0e..a1a16db3): **PASS**, no findings (docs only, one round). The verifier checked every changed ARCHITECTURE sentence against the code (nothing unbuilt stated as built), the resent-confirm rule against `PlanStore.fate` and `GatedRoute`, the §15 test on the base (17 problems) and now (pass), and each new row's milestone against `intent-table.toml`.

## Retro
- About 25 min (the slice about 10 min, the verifier about 3 min). Findings 0. The orchestrator's brief left out two of the area review's overflow items (the save's `.open` check at step 1, `ENCRYPTED_SUFFIXES` in both processes); recorded in `M1b-exit.md` by the orchestrator.
