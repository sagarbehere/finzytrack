# S0108: the user-docs line for clones of a ledger drive (BL-0222)

## Brief
- Goal: the user docs say what the owner decided for BL-0222: after a crash, plug the original drive back in, not a backup copy, because a block-for-block copy looks like the original to recovery. M1a item f(a).
- Implements: BL-0222 (owner, 2026-11-09: "Rare (a cut save, then the backup clone plugged in instead of the original); the user docs say: after a crash, plug the original stick back in, not a backup copy. No new file written anywhere."); spec `engine-jobs.md` §6.3 "Limit (drive identity)" (cited there by S0090).
- Capabilities: none changed (no product behaviour).
- Invariants touched: none (INV-23: no real data, no home path in the docs).
- Risk class / lane: Low / light (user docs at milestone end). Review budget row: docs only (verifier on facts and doc consistency, one round, no fresh round).
- Mechanism questions (§3 step 2): none; the slice adds or hardens no mechanism.
- Acceptance tests: none (docs only); the verifier checks the sentence against spec §6.3 and BL-0222.
- Expected doc impact: docs repo `finzytrack-docs`, branch `v0.4`, `src/content/docs/reference/where-to-keep-your-ledger.md` (one bullet under "a few rare cases remain"); `design/backlog.md` BL-0222 closed.
- Open questions: none.
- Base: 85f9e4ae.

## Decisions
- Placement (agent, changed in round 1 by verifier F1): first one bullet in the page's list of rare cases on drives without a safe replace; after F1, its own section "After a crash on an external drive" for every external drive, APFS too (a block-for-block copy carries the volume's identity whatever the format), linked from the APFS paragraph, since a reader with an external APFS drive skips the unsafe-drives list.
- Commit (agent, as S0065): made with `git commit-tree` on a temporary index, so the owner's checkout of the docs repo (on its `valuations` branch) was not touched; the `v0.4` branch is local and unreleased, so it is not pushed. Docs commits d9c000a (the bullet) and 9b1382a (round 1).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none.
- Self-mutation: not applicable.
- Population: not applicable.

## Verifier
- Round 1, head d904c7dc (docs commit d9c000a): pass with findings. Attacks: 8 (each claim against spec §6.3 "Limit (drive identity)", §8.1's when, §6.5's history trim, what a Finder copy does, placement against the page's APFS promise, the owner's words, the row edit, the named tools). Docs-only row of the review budget: one round, no fresh round.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Medium | d | Fixed (9b1382a): the bullet sat under the unsafe drives while the APFS paragraph promised a full restore; now its own section for every external drive, linked from the APFS paragraph |
| F2 | Low | d | Fixed: recovery also runs at a switch and at Reopen or Retry (spec §8.1, §6.4), not only at the next start; the section says so |
| F3 | Low | d | Fixed: "nothing left to repair it" overstated (the backups folder stays in the history, §6.5); now "no longer undoes it on the original" |
| F4 | Low | d | Fixed: an older copy of a disk-image file attached instead of the original, the case spec §6.3 calls common, is named |
- Open point from the verifier, not a finding: whether Disk Utility's Restore keeps an APFS volume's UUID was not probed; if it does not, the page overclaims in the safe direction (recovery stops with `wrong_drive`).

## Retro
- Build 25 min, fix 10 min, 1 verifier round, findings 0H/1M/3L (all class d). Cheaper gate: reading the whole page for promises the new sentence qualifies (the APFS paragraph) before placing it would have caught F1. Process checks: invariants 0 catch / 0 false positive; deferrals 0 / 0.
