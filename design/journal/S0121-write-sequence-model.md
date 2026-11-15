# S0121: the write core's sequence model, a Hypothesis state machine (M1b, ARCHITECTURE.md §8.2)

## Brief
- Goal: M1b's sixth slice. A test-only Hypothesis `RuleBasedStateMachine` in `engine/tests/ledger/` that checks the one thing no existing test checks (owner's choice below). It runs **sequences** of writes through the engine's real job code over the in-memory file system (`engine/tests/fakes/memfs.py`):
  - the preview, with the contract's prepare and the test producer;
  - the save (`save.run`);
  - the rebuild with its publish (`rebuild.run`) and the open (`opening.run`);
  - recover-all (`recover_all.list_journals`, `restore.recover_journal`, `recover_all.keep`, in the sidecar's order, as `test_recover_conformance.py` plays it).
  Interleaved with outside edits, a crash at a chosen effect, a chosen power-loss state, and restarts. The ledger is made of real Beancount entries, and what is checked is the ledger's entries, not only its bytes. Per-effect exhaustiveness stays with the existing sweeps (`test_writer.py`, `test_restore.py`, `test_publish_crash.py`) and the TLA+ replays; this model adds the sequence and the entry level.
- **Rules** (each a `@rule`, with preconditions where the state needs them):
  - `preview_*`: a one-transaction insert, replace and delete, and a `TokenReq` (one account token), through the test producer (`engine/tests/ledger/producer.py`), on the published generation. A `stale`, `moved` or `paused` answer is a legal outcome, recorded.
  - `confirm`: the last prepared plan saved; the outcome (saved, refused, not saved, unfinished) recorded.
  - `rebuild`, `open`.
  - `outside_edit`: a file's bytes changed between jobs. The change adds, removes or rewrites a whole entry, or touches only a comment; the reference applies it as an outside change. Durable or not.
  - `crash_during(job, k)`: the next job's k-th effect from now crashes it (`fs.inject(len(fs.effects) + k)`); the job's outcome is what the engine or §6.6's reading of the disk says.
  - `power_loss`: one durable state chosen from `power_loss_states()` (never all: `MAX_STATES`), then a restart.
  - `restart_and_recover`: recover-all before any other job (INV-32), as the sidecar orders it.
  - `read_mirror`: through `mirror.publish.read_published`.
- **The reference model:**
  - per ledger file, the expected bytes;
  - the expected multiset of the whole ledger's entry keys (`ledger.core.entry_keys`, location-free);
  - the saves the engine called saved (closed records), and which outside edits were made;
  - the last generation published and the fingerprint it was stamped with.
- **Invariants after every step:**
  1. every ledger file holds bytes the reference allows (the pre or the post of the last save that touched it, or an outside edit's), never partial;
  2. the ledger's entry multiset equals the reference's: the saved intents applied, plus the outside edits, with nothing else changed (untargeted entries unchanged across the whole sequence);
  3. no change the engine called saved is lost, unless a later outside edit or a later save replaced it, or recovery rolled back an unfinished one the engine never called saved;
  4. a refusal before the commit point (the gate, the contract, the preview) leaves every ledger file byte-identical (journals and backups may exist after a writer refusal: the claim is about ledger files);
  5. the published mirror is always a complete generation, and the gate's or the preview's freshness answer agrees with the disk: fresh exactly when the stamp's ledger set observed now has the stamp's digest;
  6. every save still in history (the trim keeps 16 per kind) has a backup holding each replaced file's pre bytes;
  7. recover-all leaves no `.open` journal it did not mark blocking, before any other job runs.
- **The load and observe ports over the fake.** The jobs' ports (`preview.Ports`, `rebuild.Ports`, `opening.Ports`, `save.Ports`) are injected with a load and an observation that read the in-memory file system. The load uses Beancount's own parser and booking on the fake's bytes, so the entries checked are Beancount's. If the loader cannot read through a hook without patching Beancount globally inside the test, a single-file root with one plain `include` is enough. **State the fidelity limit** in the test's docstring and §8.2: which loader paths the fake load does not exercise (globs, plugins, documents roots, encrypted files), each already covered by the real-disk tests that name them.
- **Run sizes:** a bounded `settings` profile for the push gate and T1. Record its example and step counts and its time on this machine in the journal; fit it to the gate's hang detector, which is relative, never a fixed budget. Add a long-run profile chosen by an environment variable for T2 and the owner's long runs. Use the repo's existing Hypothesis profile conventions if any (check `conftest.py`); the database stays out of the repo.
- **Must-not-pass, by seeded defects:** with `scripts/mutants.py`, at least 4 loosening mutants in product code that the single-job tests might not catch across a sequence. For example:
  - the rebuild stamping the hint instead of what the load read;
  - the save's gate skipping the fingerprint check after a recovery;
  - recovery leaving a `.open` journal it did not try;
  - the history trim removing a backup still needed.
  Report for each whether the model kills it and whether the existing suites do. A mutant only the model kills is the slice's evidence. One no test kills becomes a finding.
- **If the model finds a real product defect, stop**: report it with the shrunk example and do not fix it. The owner gets the policy option before any fix round (the owner's instruction for M1b's slices).
- Implements: ARCHITECTURE.md §8.2 as narrowed (owner below); engine-jobs §6.1, §6.4, §6.6, §8.1; mirror publish.md §4, §7, §8; INV-01, INV-22, INV-32 checked across sequences.
- Capabilities: none changes status.
- Invariants touched: none changed. INV-01, INV-22, INV-32 gain a cross-sequence check. INV-23, INV-24: generated data only, nothing real, no path in the repo.
- Risk class / lane: Medium / full: test-only, but it checks the write core. Review budget row: **Tooling, gates, measurement**: one verifier round; the fix round fixes only a shape the model's own docstring promises to check, a false statement of fact, or a finding in a guard of real data or privacy; a High is always fixed. A product defect the model finds is not fixed in this slice (above).
- The bytes it protects: none itself; it checks that the write core protects them across sequences.
- Mechanism questions (PROCESS.md §3 step 2): it adds no mechanism to the product. The policy question came first: the owner chose the narrowed model over a docs-only coverage map and over §8.2 in full (below). Fix-round history of what it exercises: the save (S0063, S0070, S0071, S0100-S0102), recovery (S0061: four rounds; S0062, S0101), the publish (S0087, S0088), the confirm (S0117: five rounds). The model is new.
- Acceptance:
  - the state machine runs green under the bounded profile;
  - each invariant has a self-test that a hand-broken reference or a hand-broken disk state fails it, so no invariant is vacuous;
  - each rule is reached (Hypothesis's `target` or a counter asserted at teardown over the bounded run: every rule fired at least once, and a crash during each job kind and a power loss each reached at least once);
  - the seeded mutants' results are in the Hand-off.
- Expected doc impact:
  - ARCHITECTURE.md §8.1 (the table's row: path and what it checks), §8.2 rewritten to the narrowed model, with what the existing sweeps and replays cover named once, and §8.3's stale sentence "The Hypothesis model (§8.2) checks it against the code meanwhile";
  - the roadmap's M1b "Built"/"Exit" wording if it names the old model;
  - engine-jobs §14's test list.
- Open questions: none.
- Base: 424809a1.

## Decisions
- The model is narrowed to the gap: sequences of preview, save, rebuild or open, outside edits, crashes, power loss and recover-all, checked at the level of the ledger's entries (owner, 2026-11-15, in session, choosing among "Sequence model only", "Drop the model, map coverage" and "§8.2 in full, updated": "Sequence model only (Recommended)").
- (agent, from the coverage inventory) Obsolete parts of §8.2 are dropped, not built:
  - a fake `Locks` and edits "with or without a lock" (ADR-0068 D3);
  - `legacy_write` as its own rule, which is an outside edit (ADR-0068 D4);
  - a "stale mark" on the mirror (freshness is computed, publish.md §7);
  - `move_postings` (no such intent; `TokenReq` stands in);
  - `backend/tests/models/` (the write core is the engine's; the sidecar may not import it).
- (agent) **The load is the product's own overlay load over the fake**, not a test-written loader: `memledger.py` points `overlay.path` and `overlay.fp` (the overlay's only names that touch the disk) at the fake with `unittest.mock.patch.object`, for the call only. Beancount is not patched. So the candidate overlay, the `not-reached` refusal and `Loaded.fingerprint`, from which the plan's fingerprint comes, are the product's. The brief's fallback (a single-file root) was not needed: the ledger is a root with two plain includes. The rebuild's real load (Beancount's `loader._load` on the real disk) is replaced by the same overlay load with nothing in its overlay, called as the overlay's private `_load` inside `beancount_context`, because the rebuild already wraps its load in `imports.recording` and a recording cannot nest (found at the first run). The overlay load's equality with Beancount's loader is pinned on the real disk by `test_overlay.py` and `test_parity_corpus.py`.
- (agent) **Beancount's own plugin modules are imported before the model's first load** (`memledger._warm`): the module cache answers later loads, so no load records a module lookup and every fingerprint is the same shape. In the engine each job loads in a fresh process and records them; that path is `test_plugin_fingerprint.py`'s. Stated in the fidelity limit.
- (agent) **A crash point counts the job's steps, not only its effects:** `crash_during(job, k)` fails the k-th step, where a step is an effect of the fake or a call of the load, read or observe port. This uses `MemFS.on_effect` (it fires before each effect, as `inject` would) and the ports' own hook, not `fs.inject(len(fs.effects) + k)`. The reason is that the preview makes no effect at all: counting effects only would never crash a preview. `k` is drawn over each job's usual length (`LENGTH`), so a crash after a save's commit point is reached. An I/O error (`ENOSPC`) is drawn as often as a crash.
- (agent) **Extra rules beyond the brief's list:** `keep` (the blocked screen's "Keep the files as they are now" on an unfinished save's journal, so a journal recover-all stopped on can be released, as the sidecar's screen does), a plain `restart` (the app quit with no recovery yet: INV-32's precondition then holds every other job back), and `recover` (recover-all again after a death in it). `outside_edit` may target the file an unfinished save writes (`onto_unfinished`), which is what makes recovery meet an outside change and stop.
- (agent) **The root is edited durably only:** a power loss may tear a file whose outside edit was not synced, and a torn root may lose an `include`, which would change the ledger's set of files (the model keeps the set fixed). The other two files take unsynced edits; after a power loss the reference takes what the disk kept of such an edit (the editor's loss, `power_loss:editor_lost`), and only from the bytes such an edit allows (its old synced bytes, the new ones or a tear of them).
- (agent) **A plan is confirmed once**, as the sidecar's `PlanStore.take` hands it out once; a restart drops it (the store is in memory).
- (agent) **The history check uses the spec's 16** (engine-jobs §6.5), written in the test, not `core.history.KEEP`: a mutant of the product's constant must not move the check with it.
- (agent) **Rules added after the first seeded mutants survived** (tests-first evidence, below): `write` (an insert previewed and confirmed at once, optionally with an outside comment saved between the two, `edit_between`), `crash_during_save` (the save's crash as a rule of its own, optionally followed by an edit of the file it writes), `edit_unfinished` (an outside edit of that file while writes are paused, as its own rule, precondition: an unfinished save), `purge_mirror` (the cache emptied), and Keep pressed right after recover-all (`then_keep`). Hypothesis draws rules evenly and turns random subsets off per example, so a three-step scenario as its own rule is reached in the bounded run where a chance sequence is not.
- (agent) **Checks added for the same reason:** no job that met no injected fault answers `moved` (nothing acts during a job in this world; `_never_moved`); a `loaded` publish and a `prepared` preview never beside an `.open` journal (engine-jobs §7); after a journal settles `.closed`, `.resolved` or `.kept`, each file it names has its data durable in the fake (`MemFS.synced_file`), since each of those states promises it.
- (agent) **Bounded profile: 300 examples of up to 50 steps, derandomized, no example database, shrinking on.** About 10 s on this machine (10.0 s and 10.35 s in two runs; 10.4-11.3 s after round 1's rules; the whole engine suite took 3 min 40 s with it). It is a fixed sample, so the gate is stable; the pre-push hang detector is relative (8x the median of the last 5 green runs), so 10 s needs no budget. Smaller samples (60x40, 200x50) did not reach every rule. A failure shrinks for at most Hypothesis's own 5 minutes before it reports.
- (agent) **An I/O error raised by a port the model injects** (observe, read, load) is beyond what the real observation does (`fingerprint.file_state` turns read errors into `Unreadable`); it is kept because the save then answers nothing and the model reads §6.6's outcome from the disk, which is the sidecar's path after an engine error. The freshness agreement is not asserted after such an injected error.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- **What it adds:** `engine/tests/ledger/test_write_sequence_model.py` (the machine, the reference, the invariants, their self-tests, the profiles) and `engine/tests/ledger/memledger.py` (the load, read, observe and warnings ports over the fake). No product code changed.
- **Checks that accept, tag or refuse, each with its must-not-pass test** (all test code; the self-tests break a disk or a reference by hand and expect the check to fail):
  - `inv_files` (each ledger file at bytes the reference allows) → `test_inv_files_fails_on_a_torn_file`;
  - `inv_entries` (the entry multiset) → `test_inv_entries_fails_on_an_entry_the_reference_lacks`, `test_inv_entries_fails_on_a_changed_untargeted_entry_with_bytes_the_reference_allows` (the bytes agree, the entries do not);
  - `inv_saved_kept` → `test_inv_saved_kept_fails_when_a_saved_change_is_gone`;
  - `inv_unchanged` → `test_inv_unchanged_fails_on_a_changed_ledger_file`;
  - `inv_mirror` → `test_inv_mirror_fails_on_a_torn_generation_and_on_one_no_job_published`;
  - `check_fresh_answer` (the freshness agreement) → `test_the_freshness_agreement_fails_on_a_wrong_answer` (both directions);
  - `inv_history` → `test_inv_history_fails_on_a_lost_backup_and_a_lost_record`;
  - `inv_recovered` → `test_inv_recovered_fails_on_an_open_journal_recovery_did_not_report`;
  - the reach assertion (every rule, a crash in each job kind, a power loss, a Keep done) → the bounded run itself fails when one is missing (it did, at 60x40 and 200x50, before the profile was fixed at 300x50);
  - the rule-level checks (`_gate_was_right`, `_never_moved`, the refusal reasons, the stamp check, the durability after a settle, the prepared plan's entries) have no hand-broken self-test; the seeded product mutants below are their must-not-pass evidence.
- **Self-tests are not vacuous:** each of the 8 invariant functions made a no-op (`return` first, `scripts/mutants.py`, plan in the session scratchpad, command: the self-tests only): **8 tried, 8 killed.**
- **Seeded product mutants** (`scripts/mutants.py`, plans in the session scratchpad; the copy's `engine/src` through `PYTHONPATH`; "existing suites" is the whole engine suite without this model and without `tests/architecture`, whose tests ask git about the tree and fail in the copy, which is no git checkout):

  | Mutant | The model (final bounded profile) | Existing suites |
  |---|---|---|
  | `rebuild-stamps-the-hint` (`rebuild.run` stamps the hint instead of what the load read) | killed (survived round 1; killed since `_never_moved`: the publish's own re-check refuses the stale stamp, and the rebuild answers `moved` for ever) | killed |
  | `gate-skips-the-fingerprint` (`save._gated` never compares the plan's fingerprint) | killed (survived round 3 when the sample moved; killed since `write`'s `edit_between`) | killed |
  | `listing-drops-an-open-journal` (`recover_all.list_journals` drops the first `.open` record) | killed | killed |
  | `trim-keeps-2` (`core.history.KEEP` 2: the trim removes backups of saves still in the newest 16) | killed (survived round 1: too few saves per example; killed since `write`) | killed |
  | `gate-skips-mirror-freshness` (the gate refuses `mirror_stale` only with no generation at all) | **survived** | killed |
  | `freshness-ignores-the-digest` (`publish.fresh_on_disk` checks only the root) | killed | killed |
  | `restore-skips-the-flush` (`restore` drops step 4's flush, S0101) | **survived** | killed |
  | `keep-skips-the-file-sync` (`recover_all.keep` syncs no file) | killed (survived round 3; killed since the durability check after a settle) | killed |

  After round 1 (verifier F1-F3), from the last completed run of the model's plan (`mut-model4`, before the `put_back` choice was added) and the existing suites:
  - `gate-skips-mirror-freshness`: model **killed** (was survived), existing suites killed;
  - `gate-skips-open-journal` (verifier's, both `.open` looks off): model killed (`my_mutants.py`), existing suites killed;
  - `trim-keeps-15` (verifier's): model killed (the 18-save sequence), existing suites killed;
  - `recovery-resolves-post-without-restoring` (verifier's monkeypatch): model killed, both by the bounded run and by the crash-after-commit unit test; not run against the existing suites (a monkeypatch, not a text mutant);
  - `gate-skips-the-fingerprint` survived the `mut-model4` run when the derandomized sample moved; `confirm_on_a_newer_generation` without `put_back` (plan stale, mirror fresh) was added for it and `ledger_changed` put in `REACHED`. The rerun of all mutants on that final version was still running at hand-off, so its result is not recorded here.
  - `restore-skips-the-flush` stays a stated limit.

  **No mutant is killed by the model alone**: the existing suites kill all 8. The slice's evidence of the brief's kind ("a mutant only the model kills") is therefore absent; what the model adds is shown by its own catches during development (below) and by the shapes no single-job test states (entry multisets across sequences, the freshness agreement, outcomes read after a death and then recovered). The two survivors are stated limits in the model's docstring: in this world a plan still at its fingerprint implies the generation its preview used is still fresh (no job publishes a stale generation and only `purge_mirror` touches the cache), so the gate's freshness check is unreachable defence in depth here; the flush needs an unsynced outside edit, a save cut before its first swap and then recovery, which the bounded sample rarely draws.
- **What the model found while it was built** (each a defect of the model, none of the product): a nested `imports.recording` (the rebuild's port), a plan fingerprint compared only on the changed files, `Refused.unchanged` read as "nothing changed" (it means "every target at pre"), a torn root after a power loss changing the ledger's set, an unfinished save's post keys not moved by an outside edit of another file, a saved change counted lost when a later unfinished save replaced it, an injected I/O error in the gate's observation taken for a stale mirror. Random runs (not committed): 1000x60 found the saved-change one; after the fixes, 1500x60 random examples found nothing.
- **Product defects found: none.**
- **Population:** the rules cover the job kinds of the brief (preview, save, rebuild, open, recover-all) and the outside actions it names; the inventory is the brief's rule list, and every listed rule is asserted reached (`REACHED`). Counts over the bounded run (one run; a rule's count includes calls whose precondition held but that did nothing, e.g. `keep` with no blocked journal): preview insert 702, replace 731, delete 670, token 446 (prepared 754/528/205/255; `stale` 894/203/458/187; `paused` 33); `confirm` 63 and `write` 349 (saved 186, refused `ledger_changed` 82, not saved `io_error` 39; `write:edit_between` 71); rebuild 428 (loaded 211, fresh 224); open 930 (loaded 606, fresh 639, paused 19); `outside_edit` 931; `edit_unfinished` 25; `crash_during` 663 and `crash_during_save` 494, crashes fired: preview 11, save 75, rebuild 29, open 35, recover-all 17 (I/O errors fired: 17, 81, 21, 51, 7); a save's death read by §6.6: no record 92, `.open` 22, `.part` 3; `power_loss` 1151 (an unsynced outside edit lost 115 times); `restart` 1120, `restart_and_recover` 1250, `recover` 316; recovery `Resolved` 4, `Stopped` 11; Keep done 11; `purge_mirror` 1253; `read_mirror` 964.
- **Fidelity limit of the fake load:** plain `include`s only; no plugins of the ledger's own; no documents roots or `document` files; no encrypted, linked or special files; Beancount's own plugin modules imported before the first load, so no load records a module lookup; the rebuild's real load replaced by the overlay load with nothing in its overlay. Each is covered on the real disk by the tests `memledger.py` names. The ledger's set of files is fixed.
- **Gates** (at the commit): engine `ruff format --check` and `ruff check` (`--config ruff.toml`) clean; `pyright` 0 errors, and `scripts/pyright_platforms.py engine` 0 errors on Darwin and Linux; engine `pytest` 10408 passed, 9 skipped (220 s); `check_invariants.py`, `check_generated.py --all`, `formal/tla/engine-jobs/gen_traces.py --check` green; `run_meta_tests.py` 981 tests OK.

## Verifier
- Round 1, head c8eeb35f: PASS WITH FINDINGS (no High). Attacks: 10. Review-budget row: tooling, gates, measurement (one round, no fresh one).

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Medium | c | Fixed (reach): `confirm_beside_unfinished` holds two plans (the plan store holds up to 4), cuts the second's save with its journal `.open` and the files at pre, then confirms the first; `confirm:Refused:unfinished_save` and the rule's reached branch are in `REACHED` (5 each in the bounded run). The verifier's `gate-skips-open-journal` (both `.open` looks off) is killed by the bounded run (`my_mutants.py`). A text mutant that drops only the gate's second look is equivalent: step 1's look refuses first. |
| F2 | Medium | c, d | Fixed (reach, by its own sequence): `test_the_history_keeps_the_newest_16_over_18_saves` (18 saves with their rebuilds, every invariant after each, about 0.3 s) kills `trim-keeps-15`; the false sentence in `write`'s docstring is gone, and the module docstring says the bounded run does not reach the limit. |
| F3 | Low | d, c | Fixed (reach): the false survivor sentence removed; `confirm_on_a_newer_generation` plays the verifier's sequence (preview, rewrite, open, put back, confirm) and, without the put-back, the plan-stale/mirror-fresh case; `confirm:Refused:mirror_stale` and `confirm:Refused:ledger_changed` are in `REACHED`; `gate-skips-mirror-freshness` is now killed by the model. |
| F4 | Low | d | Fixed: `LENGTH`'s docstring gives the measured counts (open 2 fresh, 15 loading; recover-all 0 with no journal folder), and `test_each_job_s_steps_fit_its_crash_range` measures each job and fails if one outgrows its range. |
| F5 | Low | c | Limit stated: the module docstring says `Unfinished` and `NotSaved(open_journal=True)` need a failed rollback, which one fault per job cannot give, and names `test_writer.py`'s faults inside the rollback; the branch is kept with a comment. |
| F6 | Low | c | Fixed: `test_inv_recovered_fails_step_by_step_on_an_open_journal_that_is_neither_unfinished_nor_blocking`. |

## Retro
