# Third round on the test-copy guard: patch the window again, or forbid links in a test copy's save?
- Asked: 2026-11-14 by the orchestrator (S0117, third fix round on one mechanism: ADR-0068 D6; the owner's standing guidance: when fixes to one thing don't settle it, stop and bring situation, likelihood, options, recommendation)
- blocks: S0117-contract-confirm (it cannot land with an unresolved High)
- Rubric item: a further fix round on one mechanism needs the owner's options note
- ADRs checked: ADR-0014; the owner's decisions of 2026-11-14 on S0117 ("Go with (2), fix it now"; "3", `20261114-test-copy-folder-identity.md`).

## Situation (plain language)
Round 2's fix (your option 3) checks, at write time, that the test copy's folder is not a link and is the folder recorded when the copy was made. Inside that check the engine looks at the folder twice: once through an open handle (for its identity) and once by its path (to decide which folder the writes must stay in). A program that switches the copy's folder back and forth between itself and a link to your real ledger's folder can make the first look see the copy and the second look see your real folder; the files, resolved a moment earlier through the link, then count as "inside", and your real ledger is written with "Saved".

The red team hit it on real APFS with no test hooks: a separate program flipping the folder succeeded after 86 tries (about 2.5 s). The verifier found the same window (rated Medium, as it needs a racing program).

## Likelihood
Not reachable today: nothing can mark a test copy until M3, so every confirm on any workspace is refused. After M3 it needs a program that deliberately races the save by flipping a folder link: not an accident, but the guard exists to keep unpromoted code off your real books, and the result would be a false "Saved" on your real ledger.

## Review history of this mechanism
S0117 round 1: RT-F1 (High: a file link swapped in after the confirm), fixed by the engine checking each file against the copy's folder. Round 2: V-R2-1 / RT2-F1 (High: the folder itself a link before the confirm), fixed by your option 3 (the folder's recorded identity, checked by the engine). Round 3: this (RT3-F1 High, V-R3-1 Medium). Each fix closed its case and left a neighbouring one, because each still follows links and then checks where they led.

## Options (cheapest first; PROCESS.md §5)
1. **Patch this window.** Take the folder's path from the same open handle as its identity (one look, not two), and add a test that swaps inside the check. A few lines. It closes this case; the pattern of the last three rounds suggests a neighbour may remain.
2. **Policy: a test copy's save follows no links at all** (recommended). Test copies are made by Finzytrack's own copy job, which copies files and never links (CAP-WS-TEST-COPY), so a link inside a test copy is never legitimate. The engine opens the copy's folder once (as now, with its recorded identity), then reaches every file it writes by walking down from that open folder one name at a time, never following a link (`DirHandle.open_child`, which the mirror publish already uses, S0087); a link anywhere refuses the save, and nothing is ever looked up again by path. This removes the whole family (file links, folder links, swaps between looks) instead of the next instance. Cost: a small engine change on the save's confined path only; a promoted mutator's save on your real ledger is unchanged (it may still follow your own links, as today).
3. **Defer to M3** (BL-0267): land S0117 with this as a stated limit and fix it when the test-copy mark is built. Leaves a known High open until then; needs your words.

## Recommendation
Option 2. It is the "forbid the rare setup" answer: no legitimate test copy has links, so refusing them costs nothing and ends a family that has needed three rounds. It reuses an engine primitive that already exists and has been reviewed.

The same round also fixes the Lows (none needs you): the "folder was moved or replaced" text will name what actually happened (no permission, a read error, an unknown drive); a FAT/exFAT folder with no creation time is refused as the docstring says; the copy job (BL-0267) must never record a folder that holds or sits above a real ledger; the stale spec and journal lines; and `PLAN_EXPIRED`'s next step will say "check your ledger" only where an earlier confirm could have saved, and "preview the change again" otherwise. Accepted, recorded as a limit: a folder's creation time can be set by a program on APFS, so the identity guards against accidents, not against a program that sets times on purpose; with option 2 that no longer opens a way to your real ledger, since no link can lead there.

## Answer (owner, verbatim, with date)
Owner, 2026-11-14, in session: "2"
