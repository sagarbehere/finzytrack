# The test copy's folder replaced by a link: how should the confirm know it is still the copy?
- Asked: 2026-11-14 by the orchestrator (S0117, second fix round on one mechanism: ADR-0068 D6, PROCESS.md §3 review budget)
- blocks: S0117-contract-confirm (it cannot land with an unresolved High)
- Rubric item: a second fix round on one mechanism needs the owner's options note (situation, likelihood, options, recommendation)
- ADRs checked: ADR-0014 (until promoted, a mutator writes only test copies: "every file the plan writes is inside the test-copy folder"), ADR-0068 D6, the owner's decision of 2026-11-14 on S0117 RT-F1 ("Go with (2), fix it now").

## Situation (plain language)
A test copy starts as a byte-for-byte copy of your real ledger, in its own folder. Until a mutator is promoted, the confirm may only write inside that folder (the guard; your rule 6). Round 1's fix makes the engine refuse any file outside the folder at the moment it writes.

Both the verifier and the red team found the same hole one level up: if the **copy's folder itself** is replaced by a link to your real ledger's folder at any time before the confirm, the app follows that link when it works out which folder the writes must stay in. It then tells the engine "stay inside" your real folder; the engine agrees every file is inside; the bytes are identical, so the "unchanged since preview" check passes; your real ledger is written and the app says "Saved". (A narrower version of the same thing: the app works out the folder twice within microseconds, and a swap between the two does the same.)

## Likelihood
Not reachable today: nothing can mark a workspace as a test copy until M3 (CAP-WS-TEST-COPY), so every confirm on any workspace is refused. Once M3 ships it needs a folder to be replaced by a link, by a person, a script or a sync tool, between the copy being made and a confirm. Rare, but the guard is the only thing between an unpromoted mutator and your real books, and the result would be a false "Saved" on your real ledger.

## Review history of this mechanism
S0117 round 1: RT-F1 (High, the same family: a file link swapped in after the confirm), fixed by the engine's folder check. Round 2: this finding (verifier V-R2-1, red team RT2-F1). The save it hands off to: S0063, S0070, S0071, S0100-S0102 (several rounds).

## Options (cheapest first; PROCESS.md §5)
1. **Defer to M3.** Land S0117 with this as a stated limit and BL-0267 (M3) fixing it when the test-copy mark is built. Cheapest now; leaves a known High open until then, which needs your words.
2. **Policy: test copies live only inside Finzytrack's own folder** (for example `<app folder>/test-copies/<name>/`). The app already checks that its own folder is the one it started with (S0102); the engine would confine an unpromoted mutator's writes to that folder and refuse any link on the way. No per-copy identity to keep. Cost: you could not put a test copy anywhere else (M3's copy job would always place it there).
3. **The copy's folder identified once, when the copy is made, and checked by the engine when it writes** (recommended). The test-copy mark records the folder's real path and its identity (drive, file system, creation time: the same identity the engine already keeps for its journal folders, S0089). The confirm sends that recorded path and identity, never following a link itself; the engine refuses unless the folder at that path is not a link and has that identity, checked at write time. This closes both versions. The mark's fields are defined now; M3's copy job fills them.

## Recommendation
Option 3. It reuses identity checks the engine already has, closes the hole where the write happens rather than in the app's earlier look, and leaves M3 free to put test copies wherever you want them. Option 2 is the simplest if you are happy for test copies to live only inside Finzytrack's folder.

The same fix round also handles the other round-2 findings (none needs you): a retry after an app restart that is told "preview the change again" will instead say to check the ledger first, since an earlier confirm may have saved it; a retry of a refused confirm will no longer be told "already confirmed"; the sidecar's save request will no longer default to "no folder"; and two test and doc gaps.

## Answer (owner, verbatim, with date)
Owner, 2026-11-14, in session: "3"
