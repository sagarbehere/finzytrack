# The promotion guard on Windows, a second fix round: fix the incomplete roots now, or decide them with the copy mark?
- Asked: 2026-11-18 by S0131-windows-backend-suite (fresh verifier round, finding R2-1)
- blocks: S0131-windows-backend-suite (its landing waited for this answer; answered the same day)
- Rubric item: none of 1-8; PROCESS.md §3 review budget: a second fix round on one mechanism needs the owner's options note (ADR-0068 D6; the standing rule of the M1b session)
- ADRs checked: ADR-0014 (promotion: unpromoted mutators write only on test copies; it does not fix how the copy's folder is compared), ADR-0068 D6 (the second-round rule itself). None settles it.

## Context (plain language, a concrete example first)
Example: a test copy whose recorded folder were `\\?\C:` (the long-path spelling of a bare drive) would let the sidecar's early guard call `\\?\C:\real\main.bean`, a real ledger, "inside the copy". So would a folder `\\nas` (a server with no share) for every file on that server.

The guard is rule 6 of the write contract: until a mutator is promoted, the sidecar confirms its plans only when every file lies inside the test copy's recorded folder (`app.workspaces.writes.is_inside`). Before S0131 it compared paths as POSIX text, so on Windows it refused every file (BL-0295). S0131 gave it Windows' rule. Round 1's verifier found V1 (class a, Low): a folder that is not an absolute path (`""`, `\`, `C:`) held a whole drive or share. You chose "Fix in the guard (Recommended)". The fix rejects a folder that the codec's absolute-path test refuses. The fresh round found R2-1 (class a, Low): that test is a codec check written to accept what the engine sends, and it also accepts incomplete roots (`\\?\C:`, `\\?\`, `\\?\UNC`, `\\nas`). Verified on macOS with the Windows rule: each of those returns "inside" for files under it.

Why it is latent, as V1 was:
- no workspace has a copy mark before BL-0267 (M3, the test copy's job);
- Windows writes stay off until BL-0087;
- the engine's writer re-checks the copy's folder by its identity when it writes, follows no link, and refuses a copy at a drive's root.

Related findings already recorded on BL-0267 decide the mark's spelling: V2 and RT-2 note that `ntpath.realpath` and the engine's `final_path` spell folders differently. They name the engine's path model (`ledger_engine.fs.pathmodel`: drive, UNC share or volume GUID as a root, `\\?\` as a spelling) as the one rule to compare by.

## Options (cheapest first; PROCESS.md §5)
1. **Policy now, rule with BL-0267.** Land S0131 as it is. Record R2-1 on BL-0267: the mark's folder is the engine's `final_path` of the folder the copy job made, never a root, checked where the mark is built. The guard then compares by the same rule as the engine (the shared spelling rule BL-0267 already has to decide for V2 and RT-2). No code now; the guard keeps this latent gap until M3, where nothing can reach it.
2. **Narrow fix now.** In `is_inside` on Windows, refuse a folder in the long-path or device form (`\\?\`, `\\.\`), and require a UNC folder to name a share and a drive folder to be `X:\` plus at least one name. About 10 lines and 5 tests, then a third verifier round on the same function.
3. **Full rule now.** Port the engine's root parsing (`pathmodel`'s drive, UNC share and volume GUID roots, `\\?\` as a spelling) into the sidecar for the guard. This also settles V2 and RT-2 now. About 40 lines, its own tests, then a third verifier round, ahead of the copy mark that decides what the input looks like.

## Recommendation
Option 1. The guard's input does not exist yet: BL-0267 builds the mark and must already decide how it is spelled and compared (V2, RT-2). Fixing the comparison now would be the third patch to one function before its input's format is known. Option 2 is the fallback if you want the gap closed in this slice.

Review history of the mechanism (rule 6's early guard):
- S0117: 4 rounds (round 1 V4 and red team RT-F1, a file outside the copy; round 2 the recorded path; round 3 links in the copy; round 4 the created-file case), no High after the fixes.
- S0131 round 1: V1 (class a, Low, fixed by your choice); red team RT-1 (Medium, class c, BL-0270 (3)), RT-2 and RT-3 (Low).
- S0131 fresh round: R2-1 (this note).
- No High in either slice.

## Answer (owner, verbatim, with date)
owner, 2026-11-18, in session (asked with the options above): "Full rule now".
Recorded in the S0131 journal; built in S0131 round 2 (`app.platform.windows_paths`, `writes.is_inside`), with a third verifier round on it.
