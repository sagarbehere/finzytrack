# S0165: G1's lens reviews and spot audit, triaged

## Brief
- Goal: triage G1's three cross-cutting lens reviews (L1 simplicity, L2 truthful outcomes across processes, L3 test honesty) and the spot audit of S0119 and S0149 (SA), all run on the frozen d3022ac4 (PROCESS.md §4 altitude 3; no High anywhere): fix the outcome and wording findings, harden the tests, record the owner's G1 decisions, and give the rest backlog rows or a dropped line. Scope added by the coordinator mid-slice: tests for the four survivors of G1's seeded-defect run, and the owner's answers to `design/inbox/answered/20261128-g1-review-proposals.md`.
- Implements: engine-jobs spec §6.4, §6.6, §11, §13; ADR-0008 (amendment of 2026-11-28); ADR-0059 (the writer's encrypted refusal); ADR-0012 F-2 (the re-hash rule after an engine death); ADR-0070 (a dated correction).
- Capabilities: CAP-DATA-ENGINE-JOBS (no status change).
- Invariants touched: INV-01, INV-22 (the outcome after an engine death; the sidecar's cross-check of the final reply), INV-05 (the publish sweeps now hold with a fake store that accepts a torn body, as the real store does), INV-23 (nothing private: finding IDs only).
- Kind: product code (shipped) for `engine/src/ledger_engine/ledger/save.py` (`outcome`), `writer.py` (the encrypted reason), the sidecar's `engine_jobs/save.py` (`_Run`) and the registry text; review row "hardening an existing write path / a write outcome text": verifier, the data-safety red team joining if the verifier reports an (a) finding, a fresh round after a product fix. Tests and docs for the rest.
- Risk class / lane: High / full (write outcome after an engine death).
- The bytes it protects: none; the outcome it keeps truthful: after an engine death, "not saved, and <file> was changed by another program" is said only of a file read and found not at its pre bytes (L2-F1); a `saved` reply is believed only after `committed` (L2-F6).
- Which real setup needs this, and can a policy forbid the rest? An unreadable ledger file after an engine death (permissions changed, a Windows sharing violation, an I/O error): rare but real; no policy can forbid another program's lock. The `committed` cross-check guards against an engine bug, no setup.
- Has this mechanism or its family needed two or more fix rounds before? The outcome reading (§6.6): S0063 (one round), S0100 (BL-0163 (b), the changed file apart). Not two rounds on the post-death re-hash; this fix aligns it with the in-save rule that already holds.
- Acceptance tests: `engine/tests/ledger/test_save_outcome.py::test_S0165_L2F1_*` (fail on base), `engine/tests/ledger/test_writer_encrypted.py::test_S0165_*` (fails on base), `backend/tests/platform/test_save_job.py::test_S0165_L2F6_*`, `::test_S0165_L2F9_*` (fail on base); the L3 and plant tests (below).
- Expected doc impact: engine-jobs §6.1, §6.4, §6.6, §11, §13, §15; roadmap §4 G1; ADR-0008 amendment; ADR-0070 correction; S0119 and S0149 journals (dated notes); PROCESS.md §3 step 7; the implementer's agent file; `design/generated/engine-job-texts.md` regenerated; backlog.
- Open questions: none; the four owner items were answered in session (below).

## Decisions
- (owner, 2026-11-28, in session, on G1 item 4, SA-F2): "Each wait counts (Recommended)". The option as put: the preview's wait and the wait from Confirm to the mirror publish must each meet ADR-0008's target (≤ 1 s demo, ≤ 5 s at 54k); the G1 pack reports the totals beside them. Recorded as ADR-0008's amendment of 2026-11-28; roadmap §4 G1, engine-jobs §6.1 and S0119's line 40 aligned.
- (owner, 2026-11-28, in session, relayed by the coordinator) on `20261128-g1-review-proposals.md`: item 1 (L1-F1) kept as is, after asking "If we keep as is, will it eventually be fixed or become no problem after M3? If yes, I think we just leave it." (the answer was yes: BL-0001's S0161 condition and BL-0312's `new_file` in M3); item 2 (L1-F2, L3-F1) "Keep; fix fake at M3 (Recommended)" (BL-0322); item 3 (L1-F3) "Keep as is (Recommended)"; item 4 (SA-F1) "Hand-off, then lands (Recommended)" (PROCESS.md §3 step 7, `.claude/agents/implementer.md`). The note moved to `design/inbox/answered/` with the answers.
- (agent) L1-F8: `save._first_changed` is gone and `outcome` uses `_rehash`; the writer's `_rehash` (over the save's handles and targets, identity-checked) and the save's (by path, used after the writer is gone) stay two: their inputs differ, so one function would need an adapter, not a simplification. No behaviour change beyond L2-F1.
- (agent) L1-F6: the writer's encrypted refusal names the resolved file (the `.gpg`/`.asc` one), not the change's path, so the sidecar's `ENCRYPTED_FILE_NOT_EDITABLE` names the encrypted file. The writer reports `unchanged` False there (it refuses before any target is re-hashed); the code's text claims no "unchanged", so this is truthful.
- (agent) L3-F3: the fake store's file is `MAGIC`, the stamp as one JSON line, then a body; `meta` reads only the first two, as the real store reads only SQLite's header and `m_meta`. MemFS's existing cut shape (the first half kept) already gives "header and stamp intact, rest missing"; the zeroed-rest shape the finding names is equivalent for such a reader, so `MemFS.tears` is unchanged (a fourth shape would multiply every power-loss sweep's states for no new reach). The sweep's oracle (`seen_by_a_reader`) now checks the body as well, so the sweeps prove the publish's order, and a test asserts the sweep meets torn generations the store accepts. The conformance replay's own store keeps no body (its model has a `part` state for any torn file).
- (agent) L2-F6's fake: `Gated` sends `committed` before a `saved` result by default, as the real engine does (§6.1 step 9); a `BLOCK` followed by `Gated` now gates too (`test_rebuild_job.py`'s landed case used a bare `saved` mapping, which no real engine sends; it is `Gated(answer())` now).
- (agent) L2-F9: one neutral text for a save, a create and a copy: "The ledger engine stopped before it finished, so Finzytrack does not know yet whether it was done." / "Check your files before you try again: trying again could do it twice."

## Dispositions (every finding ID)

| ID | Severity | Class | Disposition |
|---|---|---|---|
| L1-F1 | Medium | e | Owner's, inbox note: kept as is (BL-0001's S0161 condition before the first promotion; BL-0312 builds `new_file` in M3). |
| L1-F2 | Medium | e | Owner's, inbox note: kept (`_confined_target`/`_target` tail not factored). |
| L1-F3 | Medium | e | Owner's, inbox note: kept as is. |
| L1-F4 | Low | e | Dropped: the decision-log plumbing's consumer is BL-0154 (M2), already planned; deleting it now and re-adding it there costs more than it saves. |
| L1-F5 | Low | e | BL-0321 (remove `Saved.published` with a protocol step). |
| L1-F6 | Low | e/d | Wording half fixed: the writer says `encrypted_file` naming the resolved file (`writer._target`, `_confined_target`); step 0 kept. |
| L1-F7 | Low | e | BL-0321. |
| L1-F8 | Low | e | Partly: `_first_changed` removed, `outcome` uses `_rehash`; the writer's and the save's re-hash stay two (different inputs; Decisions). `_unreadable`/`_not_downloaded` in BL-0321. |
| L1-F9 | Low | e | BL-0321 (a behaviour change, with its must-not-pass test). |
| L1 overflow | Low | e | BL-0321 (`submit_save` without product caller, `_paused` duplicate, `Handles.on`). Kept as the reviewer noted: drive identity, shares. |
| L2-F1 | Medium | a/d | Fixed: `save.outcome` uses `_rehash` (unread: not checked, no path; a created path named only when read and present). Tests fail on base. |
| L2-F2 | Medium | d | Fixed: engine-jobs §11 row "Cancel after the commit point": the save's own outcome, the save's result. |
| L2-F3 | Low | c | Extends BL-0092 (M2), as the brief asked: one path for the composed text to the frontend. |
| L2-F4 | Low | d/e | Extends BL-0268 (M3, the confirm's route). |
| L2-F5 | Low | e | Extends BL-0091 (M3): `committed` is now recorded; deciding "saved + rebuild" with no engine goes with the real-process tests. |
| L2-F6 | Low | c | Fixed: `_Run` reads the disk when the final reply disagrees with `committed`; two tests over the fake engine. |
| L2-F7 | Low | e | BL-0320 (M3, with the confirm's route). |
| L2-F8 | Low | d | Stated: engine-jobs §6.4 limit "paused with nothing paused", indexed in §15. |
| L2-F9 | Low | d | Fixed: neutral registry text; generated texts regenerated; a test that names no one job. |
| L2 overflow | Low | e/d | Dropped: create/copy unknown reason `io_error` vs `INTERNAL` (both "not done", cause best effort, IMP-0078); `.kept` → paused text (unreachable); confined post-death re-hash follows links (the confined save has no caller until M3, BL-0322/CAP-WS-TEST-COPY); `ENGINE_STOPPED` "files safe" vs a plugin that writes (`data_safe` speaks of Finzytrack's own writes, and a read job makes none; what a user's plugin code does by itself is outside every outcome Finzytrack tells). Added to BL-0268 instead: `GatedRoute`'s WARMING could hide a waited save's outcome on a ticket poll. |
| L3-F1 | Medium | c | Owner's, inbox note: a stated limit in `memfs.py`'s docstring; BL-0322 (M3, CAP-WS-TEST-COPY). |
| L3-F2 | Low | c | Fixed: memfs docstring limit; engine-jobs §13 assumptions. |
| L3-F3 | Low | c | Fixed: the fake store accepts a body torn after its stamp; the INV-05 sweeps pass on ordering alone (90 passed), and a test asserts the sweep meets such files. |
| L3-F4 | Low | c | Fixed: `test_restore.py` R4 sweep asserts some case fired; `test_publish_crash.py` `pytest.raises(Crash)`. |
| L3-F5 | Low | e | Fixed: the always-true `i < total` dropped. |
| SA-F1 | Medium | e | Owner's, inbox note: "Hand-off, then lands"; PROCESS.md §3 step 7 and the implementer's agent file. |
| SA-F2 | Medium | d/e | Owner ruled "Each wait counts"; ADR-0008 amendment, roadmap, engine-jobs §6.1, S0119 note. |
| SA-F3 | Low | d | Fixed: ADR-0008 amendment, roadmap and engine-jobs §6.1 say the times stop at the mirror's publish (no screen until M2). |
| SA-F4 | Low | d | Fixed: dated correction in ADR-0070 after the round-3 note. |
| SA-F5 | Low | d | Fixed: S0149's lines 31, 32, 77 marked superseded in part (dated). |
| SA-F6 | Low | e | Dropped: S0149's retro mutant count (37 vs 43) is a historical tally; recounting changes no check. |
| SA-F7 | Low | d | Fixed: S0149's "Verifier verdict: pending" annotated (dated). |
| SA-F8 | Low | e | Dropped: whether S0149's red team reran after the High fix is history; the fresh verifier rounds that followed are recorded there. |
| SA-F9 | Low | e | Dropped: S0149's CI runs 12/14/16 cited without links; history, and the runs' results are restated in its rounds. |
| SA-F10 | Low | e | Dropped: S0119's F4 fixed outside the tooling stop rule; harmless, history. |
| SA overflow | Low | e | Dropped: V3-3 fixed without a row (tests only), the S0119 inbox path now under `answered/`, "Measure first" only in the journal, S0119's timeline, S0151's later field defect: all history, none a current gap. |

## G1 plant survivors (seeded-defect run at d3022ac4: 24 of 28 caught, 85.7%; product code 21 of 23)
Each new test passes on the real code and kills its plant (`scripts/mutants.py`, plans in the session scratchpad):
- P1-03 (`engine/src/ledger_engine/ledger/writer.py`, `_read_back` comparing only the length): `test_writer.py::test_S0165_a_read_back_mismatch_of_the_same_length_stops_the_save_unfinished` (one byte flipped, same length): killed.
- P2-08 (`backend/src/app/mirror/files.py`, `GENERATION.fullmatch` → `match`): `test_files.py::test_no_published_mirror_is_not_published[a_journal|a_temp|out_through_a_generation]`: killed.
- T-01 (`scripts/check_money_dates.py`, a float literal taken as exact): `Decimal(0.1)` and `Decimal(-1.5)` in `DECIMAL_OF_A_VALUE`: killed.
- T-05 (`scripts/check_staged.py`, `>= 5` → `> 5`): `test_S0165_five_dated_lines_are_a_statement_and_four_are_not`: killed.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `save.outcome`'s re-hash (no record, `.part`, `.resolved`): an unreadable target is never "changed by another program" → `test_S0165_L2F1_an_unreadable_target_is_not_checked_never_changed_by_another_program[part|resolved|None]`; the `.open`/`.kept` created path is named only when read and present → `test_S0165_L2F1_an_open_record_names_only_a_created_file_read_and_found_present[open|kept]`.
  - `_Run.run`'s cross-check: `saved` without `committed` is not believed → `test_S0165_L2F6_a_saved_reply_without_committed_is_read_from_disk`; a non-saved reply after `committed` is not believed → `test_S0165_L2F6_a_not_saved_reply_after_committed_is_read_from_disk`.
  - The writer's encrypted refusal at a resolved `.gpg` → `test_writer_encrypted.py::test_S0165_a_link_to_a_gpg_file_reaching_the_writer_is_the_encrypted_refusal`, `test_save.py::test_a_link_to_an_encrypted_file_swapped_in_after_the_gate_is_not_written`.
- Self-mutation (`scripts/mutants.py`, each run in a fresh copy with `PYTHONPATH=src:.`):
  - engine (cmd: `test_save_outcome.py`, `test_writer_encrypted.py`): 4 tried, 4 killed: `outcome-unread-counts-changed` (an unreadable file returned as changed), `outcome-open-names-any-created` (the base's rule for `.open`), `outcome-unread-is-unchanged`, `writer-gpg-changed-again`.
  - backend (cmd: `test_save_job.py`, `test_rebuild_job.py`): 4 tried, 4 killed: `saved-without-committed-believed`, `not-saved-after-committed-believed`, `committed-never-recorded`, `cross-check-dropped`.
  - G1 plants: 4 tried, 4 killed (above).
- Population: the findings are the four reports' IDs (L1 F1-F9 plus overflow, L2 F1-F9 plus overflow, L3 F1-F5, SA F1-F10 plus overflow): 33 IDs plus 3 overflow groups in, every one with a row above, 0 out. The callers of `_first_changed` (2, both in `outcome`) are both replaced.
- Golden change: `engine/tests/ledger/test_save.py::test_a_link_to_an_encrypted_file_swapped_in_after_the_gate_is_not_written` expected `Reason.CHANGED`; it now expects `Reason.ENCRYPTED_FILE` naming the encrypted file (L1-F6, the brief's fix).
- Tests on base 48077845 (the commit range starts at 52ca6ffe, which changed only S0164's journal and an oracle test): the L2-F1 tests (5 cases) fail with `path: '/L/main.bean' != None` and `'/L/new.bean' != None`; the writer's encrypted test fails with `Reason.CHANGED`; the L2-F6 tests fail (on base `Gated` has no `committed`; with only the fake's change, both fail by assertion); the L2-F9 test fails on the old text; the L3-F3 tests fail on base (no `BODY`). Pass on base by nature (strengthened assertions, `@regression-guard`): the R4 sweep's count, the publish sweeps' `pytest.raises`, and the four plant tests (each kills its plant instead).
- Gates (this sandbox): engine and backend ruff format and check clean; `scripts/pyright_platforms.py` 0 errors on Darwin, Linux and Windows for both; `lint-imports` 10 kept; `scripts/check_generated.py` clean; `scripts/check_invariants.py` clean; `scripts/run_meta_tests.py` 1032 OK; backend pytest 3095 passed, 330 skipped (the sandbox refuses `ps`), 23 xfailed; engine pytest 12588 passed, 31 skipped, 1 failed: `tests/fs/test_darwin.py::test_volume_id_is_the_uuid_diskutil_reports`, whose `diskutil info` the agents' sandbox refuses (exit 1; untouched by this slice; the files edited after that run re-run: 1957 passed).

## Verifier
One round. Head verified: 50227771 (range 52ca6ffe..50227771). Verdict: PASS WITH FINDINGS; no class (a) finding, so the data-safety red team did not join (review budget, hardening row). Attacks: L2-F1 over every record state (`.none`, `.part`, `.resolved` use the in-save rule; a file read and different is still named after an unreadable one; "unchanged" only after every file was hashed again; `.open`/`.kept` name only a created file read and present) and through the sidecar's text; L2-F6 both directions (one-way mutant killed; a correct saved reply sent to disk anyway is caught by the fake); the fake engine's `Gated` change (no earlier test made vacuous; every plain `save` reply in the backend tests is refused or not_saved); the INV-05 sweep (dropping step 5's sync fails all 10 death-at-every-effect cases; the oracle is strictly stronger); the tests on base (all claimed failures reproduce); the four plant-survivor tests (each kills its plant); the encrypted refusal's path to `ENCRYPTED_FILE_NOT_EDITABLE`; the owner quotes (verbatim across the inbox note, ADR-0008, roadmap §4, PROCESS.md §3 step 7, S0119, BL-0322); the numbers in ADR-0008's example; generated texts.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| V1 | Low | d | Fixed by the coordinator (docstrings only): `SaveOutcomeResult` (engine `protocol/messages.py`, and its generated `contracts/engine-protocol.schema.json`) and the sidecar's `OutcomeFacts` (`app/platform/engine.py`) now say a target that cannot be read makes `unchanged` false and is never named, so `unchanged` false with no path means "not checked" |

## Retro
- The G1 lens reviews found no High; the one false-sounding text (L2-F1) was a cause, not an outcome ("not saved" was true), fixed in one round with a fresh verifier.
- The seeded-defect run's four survivors became tests in the same slice; each now kills its plant.
