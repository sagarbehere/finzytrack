# S0149: offline, the rebuild is refused with the wrong cause. State it, or fix it by looking only at the files the load opened?
- Asked: 2026-11-23 by the orchestrator, for S0149-onedrive (BL-0283), after the 2026-11-23 run 15 and the fresh verifier round on 94a985e0..397fca17
- blocks: S0149 (its landing), and so S0150
- Rubric item: a third fix round on one mechanism (the cloud looks around the freshness checks and the rebuild); PROCESS.md §3 review budget, ADR-0068 D6
- ADRs checked: ADR-0070 and its notes of 2026-11-22 and 2026-11-23 (the rebuild is meant to name a cloud-only file "by a look at the set after Beancount's own load failed"); the answered note `20261122-s0149-freshness-cloud-checks.md` (option 1 rested on "the loads name the right file", which run 15 shows is false for the rebuild)

## Context
**Example V3-1.** Your ledger's `main.bean` is cloud-only and you are offline, and a rebuild runs. Since the early check was removed, the rebuild is refused as `beancount_failed`, "Beancount could not load main.bean", instead of `cloud_unavailable`.

Offline, Windows opens the file and then fails the read. Beancount's own parser turns that read error into its own load failure, so the rebuild's later look (which waits for a file-system error) never runs. Run 15 shows it on the runner (`test_offline_the_rebuild_of_a_cloud_only_ledger_is_refused_by_name`), and the verifier reproduced it.

**Example V3-2** (older, from phase 2). The rebuild's look after a failed load observes the files of its hint, the set the last build used, not the files this load opened. If Beancount fails for some other reason while a file the ledger no longer includes is cloud-only, the rebuild names that dropped file.

**What a user sees:** nothing false. A rebuild's refusal is not shown to the user; it is logged as `rebuild.not_loaded` with its reason, counts only (engine-jobs §5.x). The open, the save and Copy name the right file (run 15). Nothing is written. The verifier classes both as class (a), a false cause in an outcome. I would class them lower, since only the log carries them.

## Options (cheapest first)
1. **State both as limits.** Offline, a rebuild of a cloud-only ledger is logged `beancount_failed`; the look after a failed load may name a file of the hint. Fold both into BL-0310, which waits for the first mutator, when a rebuild's cause starts to matter to a user. The runner test expects what is true. Cost: docs and tests only, no fix round.
2. **Fix both with one narrow rule** (the verifier's proposal). When the rebuild's load fails, Beancount's failure included, ask about cloud files only among the files this load opened (`parsed`: each file is recorded before Beancount reads it, so the failing file is in it). Name the cloud-only one. It never looks at the hint's set alone. Cost: a small change in `rebuild.run`, a test on the real read path, one more CI run and one more fresh verifier round.

The verifier's other findings are tests and docs, done with either option:
- V3-3: a deleted gate test restored;
- V3-4: two rebuild tests rewritten on the real read path;
- V3-5: `engine/CLAUDE.md`'s wording;
- V3-6: BL-0310 to name the preview's `engine.internal` case.

## Recommendation
**Option 2.** I first leaned to option 1, since only the log carries the wrong cause. But the verifier's rule is narrow and principled: it looks only at files the load really opened, so it cannot name a stale or foreign file. That is the opposite of the checks just removed. It fixes both findings at once, and it makes ADR-0070's sentence true. Option 1 leaves two known false causes in the code, and the review budget says a class (a) finding is fixed.

Review history of the mechanism:
- phase 2 added the rebuild's early check;
- round 1: V3 (Medium, a);
- round 2: V2-1 (Medium, a), answered with option 1;
- round 3: V3-1 and V3-2 (both Medium, a).

There has been no High in this mechanism.

## Answer (owner, verbatim, with date)
Owner, 2026-11-23, in session: "Option 2."

**Decision: option 2.** On a failed rebuild load, cloud files are asked about only among the files that load opened; V3-3 to V3-6 done in the same round.
