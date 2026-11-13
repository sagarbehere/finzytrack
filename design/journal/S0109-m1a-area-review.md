# S0109: the M1a area review and its triage

## Brief
- Goal: M1a item f(b). Run the M1a area review (PROCESS.md §4 altitude 2, capped findings) and triage it: integrity, security or wrong-number findings become slices; the rest join the backlog. Fix the review's docs drift before the M1a exit run.
- Implements: PROCESS.md §4 (area review every 5-8 slices and at a milestone), roadmap §3.2 ("an area review by a fresh reviewer").
- Capabilities: none changed.
- Invariants touched: none (INV-32 is the subject of a backlog row, not changed).
- Risk class / lane: Low / light. Review budget row: docs only (verifier on facts and doc consistency, one round, no fresh round).
- Mechanism questions (§3 step 2): none; the slice adds or hardens no mechanism.
- Acceptance tests: none (docs and backlog only).
- Expected doc impact: `design/ARCHITECTURE.md` (F4: the ops list, recover-all built, the overlay load reachable, the `earlier_unfinished` reason in §9.2 and the engine-job codes); `design/specs/engine-jobs.md` §2's `rebuild` row (F1: the sentence "the next save queues another" is false today); `design/backlog.md` new rows BL-0255 to BL-0258.
- Open questions: none (the triage rule is PROCESS.md §4's).
- Base: 84a46f22.

## The review
- A fresh general-purpose agent, 2026-11-13, at d904c7dc, code, specs and backlog only (no journals); the full report and its repro are in the owner's private archive, `m1a/area-review-m1a-exit/`. Suites at that head: backend 2897 passed, 23 xfailed; engine 10063 passed, 9 skipped. Cap 15: 5 findings and 1 overflow.
- **No High; no class (a) or (b).** It found no way to lose or tear a ledger byte, give a false outcome or a wrong number. Every M1a exit criterion of roadmap §4 has evidence on the head.

| ID | Severity | Class | Triage |
|---|---|---|---|
| F1 | Medium | e | BL-0255 (M1b, with BL-0106): a background rebuild that ends without publishing leaves saves refused `WARMING` until a reopen; spec §2's sentence corrected here |
| F2 | Medium | e | BL-0256 (M1b, with BL-0106): a waited-for preview or save starts ahead of its workspace's queued rebuild and is refused where waiting would have worked |
| F3 | Medium | e | BL-0257 (M2, with BL-0103, BL-0154): INV-32's shared-file blocking has no input before a load, and the engine's open matches journals by key or lineage only |
| F4 | Low | d | Fixed here: ARCHITECTURE's ops list, recover-all, overlay reachability and `WRITES_PAUSED {earlier_unfinished}` |
| F5 | Low | c | BL-0258 (M2, with BL-0126): the swap test's reader is a stand-in; the sidecar's real reader never reads a real publish |
| O1 | Low | e | No row: the `Component` pattern admits `.` and `..`, but every handler taking a `workspace_key` already refuses both before touching the disk (`protocol/save.py` `_key`, `protocol/rebuild.py` `target_of`; tests in `test_open_ops.py`, `test_save_ops.py`, `test_rebuild_ops.py`). A row BL-0259 was drafted and dropped before the push (verifier F-E); the ID was never published |

- None of F1-F5 is an integrity, security or wrong-number finding, so none becomes a slice now (PROCESS.md §4). F1-F3 touch nothing user-facing today (nothing calls save or open yet); they are seams M1b and M2 settle before building on them.

## Decisions
- Triage by PROCESS.md §4 (agent). New rows sit on their first consumer's milestone; they are new rows, not moves, so no `moves with` citation is needed.
- F3's policy option changes INV-32's wording, so BL-0257 names it as the owner's (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none.
- Self-mutation: not applicable.
- Population: not applicable.

## Verifier
- Round 1, head b9bb3100: pass with findings. Attacks: 10 (the ops list against `OPS` and the schema; recover-all built; overlay reachability; the `WRITES_PAUSED` reasons across ARCHITECTURE; the §2 row against `save.py`; each new row against the code; O1 by running the handlers; the triage against §4). Docs-only row: one round, no fresh round.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F-A | Low | d | Fixed: the overlay load is reached only through `open`; the rebuild runs Beancount's own load (ADR-0068 D1) |
| F-B | Low | d | Fixed: ARCHITECTURE §9.2 step 8, later saves get `{earlier_unfinished}`, the open and rebuild `{unfinished_save}` |
| F-C | Low | d | Fixed: "Update now" has no row; spec §2 and BL-0255 say so and point at BL-0092 |
| F-D | Low | d | Fixed: BL-0256 says its fix changes ADR-0064 decision 2's ordering and is the owner's |
| F-E | Low | d | Fixed: BL-0259 dropped (handlers already refuse `.` and `..`; the review's O1 recorded above with no row) |

## Retro
- Build 30 min (the review agent itself about 8 min), fix 10 min, 1 verifier round, findings 0H/0M/5L (all class d). Cheaper gate: checking each new row's claim against the code (running the handlers for O1, grepping the backlog for "Update now") before writing it would have caught F-C and F-E. Process checks: invariants 0 catch / 0 false positive; deferrals 0 / 0.
