# M1a exit: Read core and workspace-format core (2026-11-13)

The milestone's exit record (roadmap §3.2: "the milestone's journal entry"). M1a ran from 2026-10-26 (M0's exit) to 2026-11-13: slices S0035 to S0113 (79 journals).

## Decision

- Owner, 2026-11-13: "Agreed, close M1a" (in session, after the green exit run at 23b8b5d6).
- `FT_MILESTONE_EXIT=M1a python3 scripts/check_invariants.py` passes (exit 0) at the exit commit 23b8b5d6: no invariant pending and no backlog item open that is due by M1a.

## Evidence

- **Manual CI run (ADR-0060), milestone exit:** the seventh run of 2026-11-13, at 23b8b5d6, started with `milestone_exit: M1a`: **success**. Green: `repo-checks` (meta-tests, privacy scan, staged-file guard, invariants with the exit check, capabilities, generated artifacts, licences), `backend`, `engine` (strict pyright as macOS and as Linux, S0113), `frontend` (the acceptance flows through the harness on Linux, Chromium and WebKit, and Chromium in Kolkata), `shell`, `oracle-unit`, `cargo-age`, and the packaged app on all three systems: macOS and Windows built, installed and smoke-tested strictly (`FT_SMOKE_OK` with Beancount 3.2.0, and the must-not-pass runs without the engine and without the interpreter); Linux's `.deb` the same, strictly; Linux's AppImage refused with exactly its known reason, `fixme` due M11 under ADR-0061 decision 4b (owner, 2026-11-13: "Defer to M11 (Recommended)", narrowed: "Narrow it (Recommended)"; BL-0263). Windows' build-check false positive was fixed in S0114 (owner: "Fix the check now (Recommended)"; BL-0264 closed). The runs before it, their failures and fixes are in `S0112-m1a-exit-ci.md` and `S0114-packaged-fixme.md` (the first CI run since M0 found: pyright on Linux, the harness's missing engine environment, a machine-specific mirror snapshot, S0113's ten Linux test failures, and Windows' build-check false positive).
- **macOS green (ADR-0061 decision 4a), at 23b8b5d6:** the T0 pre-push gate passed (the push `70d7d512..23b8b5d6`), and the acceptance suite passed locally through the harness: 39 passed, 21 skipped (the integrity flows A1, A2, A4 (two), A7, A10 and A13 in each of the three browser projects, `fixme` for their later milestones), 30 s.

## Roadmap M1a exit items (roadmap §4)

| Item | Where |
|---|---|
| The process re-measured (ADR-0058: product against process code, building against review-and-fix time) | S0106 (`S0106-measurements.md`); the owner's reflection and the review budget that followed, S0107 |
| D2 equivalence exact on the corpus | `engine/tests/ledger/test_loads_same.py`, `test_overlay.py`; the parity corpus (S0094) |
| Mirror swap test with zero read errors | `engine/tests/mirror/test_publish.py` (S0087); limit: its reader is a stand-in, the sidecar's real reader against a real publish is BL-0258 (M2; area review F5) |
| QueryRunner attacks refused | `backend/tests/mirror/test_query.py`; one QueryRunner, `backend/tests/architecture/test_one_query_runner.py` (S0103) |
| One load per operation, no resident ledger (INV-10) | `engine/tests/architecture/test_no_resident_ledger.py` (macOS; off macOS its jobs refuse before loading, S0113) |
| `/health` under 100 ms during a 54k load | S0106: at most 32 ms (the route-side check is BL-0126's, M2) |
| The protocol overhead per write and per large BQL result measured | S0106: 25-31 ms per save at 54k; a full-postings BQL reply would be about 80 MB, so paging moved to M7 with BQL (BL-0082, owner) |

## §3.2 items, as they apply to M1a

- **Release-blocking capability rows, demo fragments, user docs:** M1a has no capability row (nothing user-facing). Its user-docs lines are on the docs repo's `v0.4` branch: "Where to keep your ledger" (S0065, S0108).
- **Area review by a fresh reviewer:** S0109 (report in the owner's private archive, `m1a/area-review-m1a-exit/`): no High, no class (a) or (b); its Mediums are BL-0255, BL-0256 (M1b) and BL-0257 (M2). Earlier in M1a: the engine and backend core (2026-10-26) and the save path (2026-11-01).
- **UX red team:** no vertical with UI yet.
- **Reflection cycle:** the process reflection of 2026-11-12 (`design/inbox/answered/20261112-process-reflection.md`) and S0107 (IMP-0065 to IMP-0073); IMP-0074 (S0113).
- **Owner time and process metrics:** process metrics in S0106 and the reflection (S0096-S0106: 8.35M subagent tokens, about 65% on review and fixing; findings by class). Owner time was not measured in M1a.
- **The architecture explainer:** refreshed after this record (link in `design/README.md`).

## Formal models (beside the exit, not before it)

The owner's medium and large runs (BL-0110, moved to M1b, whose exit needs them): every world passed except `L_save_deep`, whose violation was the model's own (a reverted edit on a file whose backup the model did not represent; S0111 widened the property, owner: option B). On the widened model the rerun of `L_save_deep` had found no violation in 1.79 billion distinct states, every behaviour up to 67 steps, with its queue shrinking (55.6M, from a peak near 72M), when this record was written (2026-11-13, 23:25); `L_world` and the mirror-publish `L_all` (paused, then resumed) follow it.

## Carried into M1b and later

- BL-0110: the long model runs, M1b.
- BL-0087: the Windows runner for the engine's write-contract tests, required at every exit from M1b on; with it BL-0260 (the Windows type-check pass), BL-0261 (the boot's reason off macOS), BL-0262 (Linux inode reuse and the app-folder identity).
- BL-0219: the network-share probe, deferred to M11 (owner).
- BL-0263: the Linux AppImage's engine, M11 (narrowed to its known failure, S0114). BL-0264 closed in S0114.
- BL-0085: the legal review of TatSu-LTS, regex and dateutil, M12.
