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

## Verifier

## Retro
