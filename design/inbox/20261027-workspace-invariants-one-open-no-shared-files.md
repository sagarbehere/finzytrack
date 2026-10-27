# May INV-28 and INV-31 be reworded, and INV-32 added, for "one open workspace, no file in two workspaces"?
- Asked: 2026-10-27 by the implementer of S0045 (one workspace at a time)
- blocks: only the `design/INVARIANTS.md` edits proposed here; S0045 lands without them, and ADR-0063 is the rule meanwhile
- Rubric item: 4 (changing an invariant: its text and its "Enforced by" line are yours; precedent `answered/20261025-inv-24-enforced-by.md`)
- ADRs checked: ADR-0063 (records your decision of 2026-10-27, but your words do not approve invariant text), ADR-0062 (its approval covered INV-26 only), ADR-0018 (decided the old INV-28 wording), ADR-0003 (numbering: a new invariant takes the next free ID, INV-32), ADR-0017, ADR-0019, ADR-0041. None approves these edits, so the rubric's "always the owner's" applies.

## Context (plain language, a concrete example first)
You decided today that Finzytrack has one workspace open at a time, and that no file belongs to two workspaces. ADR-0063 records it. Three invariants should say so, and only you change invariants.

Example. You run Finzytrack headless and have two browser tabs. Tab 1 shows *Personal*. In tab 2 you switch to *Business*: Finzytrack closes *Personal* and opens *Business*. Back in tab 1 you press Save. That save names *Personal*, so it is refused ("Finzytrack switched to *Business* in another window."), and nothing is written anywhere. It must never be saved into *Business*.

1. **INV-28** says "There is no server-side 'active workspace'". Now there is one open workspace on the server. What INV-28 protects still holds, because a request is still served only for the workspace it names. But the sentence is no longer literally true, so it should be reworded.
2. **INV-31** ("Only the active workspace is touched") is the natural place for "one open workspace; switching closes the current one".
3. **No file in two workspaces** becomes a data-safety rule. The next scheduler design (draft 4) drops its protections for shared files because of it. If someone broke the rule later, a half-finished save in one workspace could be overwritten from another. That makes it worth an invariant with its own test.

The other workspace invariants were checked and need no change: INV-25 (forget still deletes no file; forgetting a workspace with an unfinished save is refused, which removes nothing), INV-27 (the check uses only machine-local records, never the folder), INV-29 (a shared-file refusal is a failed assessment and records nothing), INV-30 (the switch adds no write to any folder). The INV-31 exception that the scheduler draft 3 proposed (your answer (b) of 2026-10-27: repair a closed workspace's save from an open one) is no longer needed and is not proposed.

## Options
1. **Apply all three edits below.**

   **INV-28, rule (new text):** "Every workspace-scoped request names its workspace in the path, and is served only when that workspace is the one open in this instance (ADR-0063); a request naming any other registered workspace is refused with `WORKSPACE_NOT_OPEN` and changes nothing. No request finds its target from server-side state, and a request, response or write can never act on a workspace other than the one named. `{ws}` resolves only in the caller's registry."
   **INV-28, why (new text):** "A shared 'active ledger' pointer caused wrong-ledger writes (review:10 I6, review:15 L10-01); two tabs must not retarget each other: a switch in one tab makes the other tab's requests fail, never land in another workspace."
   **INV-28, enforced by (add):** "a switch test: after A→B, a request naming A, a save included, is refused with `WORKSPACE_NOT_OPEN` and leaves A's and B's files and the registry byte-identical." The two-tab acceptance flow stays, and now covers this case. BL-0028 grows by that test.

   **INV-31, title (new):** "One open workspace, and only it is touched"
   **INV-31, rule (new text):** "An instance (in hosted mode, a user container) has at most one open workspace; only the open workspace accepts requests or runs jobs. Opening another is a switch: it assesses the target first, then quiesces and closes the current workspace, losing and half-applying nothing (ADR-0063). Closing a workspace releases its caches and handles. Cache bounds are configurable limits, global across workspaces and generous on desktop, never fixed development targets."
   **INV-31, enforced by (add):** "the switch test (at no moment do two workspaces accept requests or run jobs; after A→B no container, cache or handle of A remains); a quiesce test: a save confirmed in A before the switch completes in A, or is refused with A unchanged." The A→B→A ×20 memory check stays. BL-0031 grows by these tests.

   **INV-32 (new): No file in two workspaces.**
   - Rule: "No file belongs to the ledger sets of two registered workspaces. Two sets overlap when a file is in both, a file of one lies inside a documents folder of the other, their documents folders are the same or nested, or a glob include of one matches a file of the other; 'the same file' is the write path's canonical identity (`path_key`), plus device and inode for existing files. Registering, adopting, creating or opening an overlapping workspace is refused with the shared file named, and an overlap that appears later is refused when either workspace computes its set (ADR-0063)."
   - Why: "One workspace's half-finished save must never be read or overwritten from another; the write path and the scheduler rely on each file having one owner (ADR-0063)."
   - Enforced by: "planned: an overlap corpus (the same file through a symlink, a case variant, a Unicode-normalization variant, a hard link, a glob, a documents folder, nested documents folders) refused at register, adopt and open, with nothing recorded (create and the test copy join with their mutators in M3); an overlap introduced by an outside edit refused at the next open and at the rebuild, the rebuild not published; a workspace with an unresolved journal cannot be forgotten."
   - Status: "pending (BL-0103, by M2)".

2. **Apply only the INV-28 rewording** (the one whose text is now wrong), and leave the one-open and no-overlap rules to ADR-0063 and the tests of BL-0102 and BL-0103 alone. Cheaper, but nothing in `check_invariants.py` would then insist those tests exist before M2 exits.
3. **Change nothing.** INV-28 would keep a sentence that the design contradicts, which the next reviewer will report.

## Recommendation
Option 1. INV-28's sentence is now wrong, and the two rules you decided today carry data safety: the scheduler will stop guarding against shared files because of INV-32. An invariant makes CI demand their tests before M2 can exit.

## Answer (owner, verbatim, with date)
