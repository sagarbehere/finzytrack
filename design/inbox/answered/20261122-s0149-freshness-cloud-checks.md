# S0149: the cloud checks added to the freshness looks can name another ledger's file. Remove them, or guard them?
- Asked: 2026-11-22 by the orchestrator, for S0149-onedrive (BL-0283), after the fresh verifier round on bf2c5cd0..5749632d
- blocks: S0149 (its landing), and so S0150
- Rubric item: a second fix round on one mechanism needs the owner's options note (PROCESS.md §3, review budget; ADR-0068 D6)
- ADRs checked: ADR-0070 (the engine's placeholder mode; its 2026-11-22 note states "every freshness look asks for a cloud-only file before answering not fresh", which is the behaviour in question), ADR-0015 and the mirror publish spec (S0087 round 1 F2: a generation of another ledger is not this ledger's). None settles which way to fix it.

## Context
Round 1 of S0149 found (verifier V3) that, offline, a change to a ledger with a cloud-only file would be told "wait a moment and try again" (`WORKSPACE_NOT_READY {WARMING}`), which never comes true while offline. The fix added an early "is a file cloud-only?" check in three places that look at whether the mirror is fresh: the preview, the save's check of the published mirror (`_stamp_not_downloaded`), and the rebuild's start (from phase 2).

The fresh round found (V2-1, Medium, class (a)) that those checks look at the **files the mirror was last built from**, not at the ledger as it is now:
- Example R1: you moved your ledger from a OneDrive folder to `C:\Books`. The old mirror still lists the OneDrive files, and one of them is cloud-only, and you are offline. A change is then refused with "OneDrive\Books\main.bean is only in the cloud … could not check whether your ledger changed", naming a file that is not in your ledger. Before round 1 this was told truthfully ("not ready; the mirror is being rebuilt").
- Example R2: your main file stopped including `inc.bean`, which is cloud-only. The preview (and, since phase 2, the rebuild's start) refuses naming `inc.bean`, which is no longer a ledger file.

No byte is at risk: every case still writes nothing. The false part is which file the text names, and saying the ledger could not be checked when it had just been found unchanged.

**Likelihood:** low today, and **no user can reach the preview or the save path yet**: they need a mutator (`preview.PRODUCERS` is empty until the first one lands, M4/M5). The open and the rebuild are reachable, and they name the right file through their own loads.

## Options (cheapest first)
1. **Remove the three early checks.** The loads and reads themselves already name the right file. That includes the open, the rebuild after its load fails, the save's gate on the plan's files, and Copy. The preview and the save's mirror check go back to "not ready" while offline, which is truthful but unhelpful ("wait a moment" never comes true). That gap becomes a backlog row that moves with the first mutator. Cost: deleting code and its tests, small; a fresh verifier round on that change. It also removes R2, which is older than round 1, and the verifier's surviving mutant M3, the rebuild's early check, which round 1 already found redundant.
2. **Guard the checks.** Ask about cloud files only when the mirror is this ledger's (`ours`/`same_ledger`) and only for files still in the ledger. That keeps V3's named reason. Cost: a small product change in two or three places, new tests, and a fresh round. R2 needs the "still in the ledger" part, which the rebuild's start cannot know before its load. So the rebuild's early check would go anyway, as in option 1.
3. **Leave it, as a stated limit.** It's not reachable until a mutator exists. Cost: nothing now, but a known false text sits in the code until then. Not recommended: a class (a) finding is always fixed (review budget).

## Recommendation
**Option 1.** It is the smaller mechanism and removes code instead of adding it. Everything still reachable today keeps a true and named outcome, and the "wait a moment" gap waits for the first mutator, which is when a user could meet it. Then the named reason can be built against the real producer.

Review history of the mechanism, the freshness-look cloud checks:
- added in S0149 phase 2 (the rebuild's start) and round 1 (the preview, the save's mirror check);
- the first verifier and red-team round found V3 (Medium, class (a)), which led to this check;
- the fresh round found V2-1 (Medium, class (a)) in the fix;
- no High in this mechanism. The High of round 1 (V1, a false "unchanged") was in the save's re-hash and is fixed and verified.

## Answer (owner, verbatim, with date)
Owner, 2026-11-23, in session: "Remove the three early cloud checks"

**Decision: option 1.** The three early cloud checks (the preview, the save's mirror check `_stamp_not_downloaded`, the rebuild's start) are removed; the preview and the save's mirror check offline go back to "not ready" (truthful), a backlog row that moves with the first mutator. Recorded in `design/journal/S0149-onedrive.md`.
