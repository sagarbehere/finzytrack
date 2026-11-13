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
- Placement (agent): one bullet in the page's list of rare cases, which is where a user who keeps a ledger on a stick reads about crashes and power failures. It says "any external drive, APFS too", since a block-for-block copy carries the volume's identity whatever the format, although the list's other bullets are about drives without a safe replace.
- Commit (agent, as S0065): made with `git commit-tree` on a temporary index, so the owner's checkout of the docs repo (on its `valuations` branch) was not touched; the `v0.4` branch is local and unreleased, so it is not pushed. Docs commit d9c000a.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none.
- Self-mutation: not applicable.
- Population: not applicable.

## Verifier

## Retro
