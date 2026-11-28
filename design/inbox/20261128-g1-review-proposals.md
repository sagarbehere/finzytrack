# G1's lens reviews and spot audit: four choices that narrow your earlier decisions
- Asked: 2026-11-28 by the coordinator, at G1 (the reviews ran on d3022ac4)
- blocks: nothing (no item holds a slice; each waits for your answer; the G1 evidence pack lists them)
- Rubric item: 4 (items 1-3 narrow or keep mechanisms decided in ADR-0012 and its amendments, S0117 and BL-0244; item 4 changes the process, PROCESS.md §3)
- ADRs checked: ADR-0008, ADR-0012 (amendments for BL-0244), ADR-0014 (the confined save), ADR-0068. Each decides the mechanism an item would narrow, so none settles it.

## Context (plain language, a concrete example first)
At G1 three cross-cutting lens reviews (simplicity; truthful outcomes across processes; whether the tests prove what they claim) and a spot audit of S0119 and S0149 ran on the write core. **None found a High.** Integrity and wording findings go to one fix slice (S0165, `design/journal/S0165-g1-review-findings.md`); the rest become backlog rows there. Four items need you, because they would undo or narrow something you decided, or change the process.

Example for item 2: when a future test copy (M3) saves, the engine checks every file it writes stays inside the copy's folder. That walk was built in S0117 (four rounds). Today nothing in the product can reach it (no workspace carries the copy mark, no mutator is promoted). The test-honesty lens then showed a one-line bug planted in it (pick the first of two names that fold equal, instead of refusing) passes all 11,347 tests, because the fake file system folds names only the way macOS does and can never hold two such names.

## Options (cheapest first; PROCESS.md §5)
Each item is answered on its own.

1. **The create branch (L1-F1).** Saves that create a file are built through the save, writer, restore, recover-all and journal, but no producer can plan one until M3 (`new_file`, BL-0312); the engine accepts a create at any path, which is why S0161 had to state "the engine trusts the sidecar here".
   - (a) Keep as is.
   - (b) **One-line refusal:** `save._check` refuses `pre is None` with `create_not_built` until BL-0312 lifts it, plus a test (about an hour; no ADR change; the code stays, dormant; S0161's trust statement becomes moot).
   - (c) Remove the create branches and build them with BL-0312 (one slice, about 60 tests touched).
2. **The engine's confined save (L1-F2) and the fake's name folding (L3-F1).**
   - (a) Keep as is; state L3-F1 as a limit; a row on M3 (with CAP-WS-TEST-COPY, the first caller) for the fake's per-folder name rules (ext4 exact, NTFS upper, "reported wide, compares exact") and the must-not-pass test of two fold-equal names.
   - (b) Do the row's work now (a test-only slice, a day or so) and factor the duplicated tail of `_confined_target`/`_target` (no behaviour change).
   - (c) Remove the engine's confined walk now and rebuild it with CAP-WS-TEST-COPY in M3 (your S0117 decisions "3" and "2" then wait as text until M3).
3. **The app folder's identity, checked in five layers (L1-F3).** Engine start, every op, the save's folder creation (read twice), the writer's anchor (three checks within microseconds), and Linux's creation time. BL-0244's own row: no ledger byte is lost if the app's folder is replaced while it runs.
   - (a) Keep as is.
   - (b) Narrow to the start check, the per-op check and one anchor check right before the journal's `.part`→`.open` rename; drop `Commit.anchor = None` (a test seam in a product type). A small hardening slice (verifier, fresh round after the product fix).
4. **"Lands" commits before the verifier (SA-F1).** In S0144, S0146, S0148 and S0149 the implementer's hand-off commit said "lands" and marked rows done; the verifier then found a High in S0149 (fixed in its round). Nothing false shipped (the push waits for the verifier), but the history says "landed" before it was.
   - (a) Wording rule: the implementer's commit says "hand-off" and leaves its rows `in-progress`; the commit after the verifier's PASS marks them done and says "lands" (PROCESS.md §3 step 7, one sentence; the implementer's agent file one line).
   - (b) Keep the practice and change PROCESS.md to describe it.

## Recommendation
1 (b), 2 (a), 3 (a), 4 (a).
- **1 (b):** the create branch came with the journal's design (ADR-0012), never had a caller and never had a fix round of its own; S0161's security F2 is its one review finding. One line removes the risk class without touching tested code.
- **2 (a):** the confined save had four rounds in S0117 (data-safety red team, Highs on the engine side), two in S0131 (Windows roots), one in S0161 (`..`). Removing it would discard paid-for work that M3 needs; doing the fake work now buys nothing before M3. The mutant is reachable only through the confined save.
- **3 (a):** BL-0244 was one slice in M1a (your "BL-0244 slice in M1a (Recommended)"); narrowing it buys no visible behaviour, and S0162 dropped a similar redundancy for the same reason. Choose (b) if you weigh cleanliness higher.
- **4 (a):** wording only; the record matches what happened.

## Answer (owner, verbatim, with date)
