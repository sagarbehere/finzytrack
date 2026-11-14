# S0111: the engine-jobs model counts a reverted edit on every file a save writes (BL-0110)

## Brief
- Goal: the owner's long runs of the engine-jobs model (BL-0110, 2026-11-13) found one surprise: `L_save_deep` violated `ExternalEditNeverSilentlyLost` after 13 min (55.0M distinct states, 11.5M left on the queue). The trace: an editor writes N, a save starts on N; after the save's temp of N is synced the editor changes N and changes it back to the bytes the save planned against; the swap displaces those bytes, finds them equal to the save's pre bytes and removes them as ours. That is spec §6.3's "ours ... the pre bytes (the backup holds them) ... deleting it loses nothing", and the real save backs up every file it replaces. The property's exception for this revert was written for R only, because the model writes one backup per save, R's. Widen it to every file the save writes, then rerun the small tier and `L_save_deep` to the end.
- Implements: the owner's choice of option B (owner, 2026-11-13: "Yes, go with B"); B was "widen the property's exception to every file the save replaces, as the real save backs up every file it replaces; one verifier round; the small tier; `L_save_deep` rerun". PROCESS.md §3 "Designs checked by a formal model" (a long-run finding that is not a High data-safety issue).
- Capabilities: none changed.
- Invariants touched: none (INV-01, INV-22 unchanged; the model's property is loosened to match spec §6.3, which is a loosening of a check and so the owner's: approved above).
- Risk class / lane: Low / light (formal model; no product code). Review budget row: tooling (one verifier round; it guards no real data or privacy, so no fresh round).
- Mechanism questions (§3 step 2): the slice changes a check, not a product mechanism. Its family (the same-bytes exception of `EditKept`) was changed once before (S0101 round 1, verifier F2).
- Acceptance tests: the small tier, every mutant still caught (`no_displaced_check`, `plain_create`, `rollback_skips_temp_rule`, `x_keep_and_go_on` violate `ExternalEditNeverSilentlyLost`); `L_save_deep` passes to the end; the other medium and large worlds rerun on the new model.
- Expected doc impact: `formal/tla/engine-jobs/EngineJobs.tla` (`EditKept` and its comment), `MAPPING.md` (the backups row and the property row), `HOW_TO_RUN.md` (the measured times of the medium and large tiers), `design/backlog.md` BL-0110 and BL-0109.
- Open questions: none.
- Base: 8165679a. Model sha256 before: 54598182c2c0.

## Decisions
- No new state (agent): changing `bk` to one backup per file would change the trace corpus that `scripts/tests/test_tla_traces.py` pins and the conformance replays read. The pre bytes of every file of `Tg` are already recorded in `gh.pre[s]`, and the real code writes every backup in one step (`writer._backups`, §6.1 step 8.1), for which the model's `bk` stands. So the exception reads, for any file of `Tg`: the editor's bytes equal that save's pre bytes of the file, the backup step is written (`fs.bk[s] = gh.pre[s]["R"]`, not ABS) and the file holds that save's post bytes. For R it is the old disjunct exactly: `bk[s]` is only ever `Pre("R") = gh.pre[s]["R"]`, ABS or TORN.
- The earlier medium and large results stay valid on the new model in logic (only an invariant was weakened, no action changed), but `run_all.sh` marks them stale on the new model's hash, so they are rerun for a clean summary.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: `ExternalEditNeverSilentlyLost` (and `RecoveryNeverClobbersForeignBytes`, which uses `EditKept`): must-not-pass: the mutants `no_displaced_check`, `plain_create`, `rollback_skips_temp_rule`, `x_keep_and_go_on` (small tier, all violate as expected after the change).
- Self-mutation: the model's own mutants above serve; `no_displaced_check` is the loosening that would hide a lost revert, and it is still caught.
- Population: not applicable.

## Runs
- Small tier on the new model: all 67 as expected (14 base, 29 mutants, 24 witnesses).
- Medium and large tiers on the new model (model sha256 02e556707512, the owner's Mac, 7 TLC workers, beside the mirror-publish `L_all`), 2026-11-13 10:41 to 2026-11-14 02:19: all as expected. `M_recover_temps` 5 s (85,156 distinct states); `L_save_all` 61 s (670,086); `L_recover_all` 243 s (4,088,438); `L_save_deep` 55,417 s (2,167,593,483 distinct states, depth 98: the world that flagged the revert on N now passes in full); `L_world` 512 s (simulation). With the small tier: 72 configurations, ALL AS EXPECTED (BL-0110).
- Close (2026-11-14, after the runs, as round 1 planned): `EngineJobs.tla`'s `EditKept` comment cites §6.4 (F3) and says the S0101 tightening is incomplete, pointing at MAPPING.md's "Limit (older backup)" (F1). Comment only: model sha256 9988da5bf4c9; the trace corpus regenerated, its header's model hash the only change (traces byte-identical). `HOW_TO_RUN.md` records the medium and large times and that they were measured at the hash before this comment change.

## Verifier
- Round 1, head aa512c70: pass with findings. Attacks: 9 (the backup stand-in against `writer._backups`, which backs up every replaced file, data synced, folder synced, before the `.part`; a power loss between `S_Backup` and `S_BackupSync`; a created N; R absent at pre; R's old and new disjuncts proved equal by TLC, 418,289 states; vacuity for N, reached in 1,054 states; a second save with a rollback; the four mutants from scratch; the corpus diff). Tooling row: one round, stop rule.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Medium | c | The comment's claim that S0101's tightening separates the cases is false (a later save's rollback back to an older save's post bytes lets that older backup hide a revert, under `no_displaced_check`; for R since S0101, for N since this slice; the base model holds). False statement fixed (MAPPING.md now; the `.tla` comment after the reruns, since the runner hashes the whole model file); the weaker check becomes MAPPING.md's "Limit (older backup)" (stop rule, IMP-0066) |
| F2 | Low | c | No small-tier witness reaches the exception: MAPPING.md's "Limit (no witness)" (stop rule) |
| F3 | Low | d | Fixed: the citation is §6.4, not §6.3 (MAPPING.md now; the `.tla` comment after the reruns; the brief's "§6.3" was the same slip) |

- Close round (docs row, one round), head 0dda9f50: pass with findings. Attacks: 9 (the models diffed with comments stripped: identical; the corpus with its hash masked: byte-identical; both model hashes; every time and count against SUMMARY.md and the logs; the `stale` claim against `run_model.sh`; the §6.4 citation; the comment against MAPPING's limit; the retro line's form; the severity counts).

| ID | Severity | Class | Disposition |
|---|---|---|---|
| C1 | Low | d | Fixed (a false statement of fact): MAPPING.md's State line still said 28 mutants, 23 witnesses, 65 configurations and the medium and large tiers not run; now 29, 24, 72 and all run. HOW_TO_RUN.md's "14 TLC workers" now reads as the S tier's only |

## Retro
- Build about 20 min (model change and small tier), fix 10 min (round 1's limits), 1 verifier round, findings 0H/1M/2L (F1 c, F2 c, F3 d); the long runs took 16 h beside M1a's exit, the close 10 min. Cheaper gate: a small-tier witness for the same-bytes exception on N (`EdFiles = {N}`, `MaxEdits = 3`, about 1,000 states; MAPPING.md "Limit (no witness)") would have flagged the R-only exception in seconds instead of in `L_save_deep`'s 13th minute. Process checks: the trace corpus check (`gen_traces.py --check`) 0 catch / 1 false positive (it hashes the whole model file, so a comment-only change refused a corpus whose traces were current; the regeneration cost 5.5 min for a header hash); the others 0 / 0.
