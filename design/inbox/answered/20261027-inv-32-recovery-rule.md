# May INV-32 state the recovery rule, and may INV-30's exception say plainly where recovery writes?
- Asked: 2026-10-27 by the implementer of S0045 (one workspace at a time), fix round 2
- blocks: only the `design/INVARIANTS.md` edits proposed here (INV-32 new, INV-30's exception sentence); S0045 lands without them, and ADR-0063 decision 2 is the rule meanwhile
- Rubric item: 4 (adding an invariant and changing one's text are yours; the S0045 verifier's round 2, F-A: the territory text applied as INV-32 was wording you had not seen)
- ADRs checked: ADR-0063 (records the recovery rule you approved on 2026-10-27, "Yes, write both into the hand-off and give me the prompt", but that approves the rule, not invariant text), ADR-0012 (the journal and its recovery; amended by ADR-0063 for when recovery runs), ADR-0003 (numbering: the next free ID is INV-32, still reserved), ADR-0062 (single instance). None approves invariant text, so the rubric's "always the owner's" applies.

## Context (plain language, a concrete example first)
Example. *Personal* and *Business* both include `prices.beancount` (not supported, but people do it). You save in *Personal*, and the power goes off half-way. Finzytrack's write journal for that save, kept in its own app data, knows which files the save changed or created and what they held before.

Next morning you start Finzytrack, and it opens *Business*. Before *Business* opens, Finzytrack undoes the half-finished *Personal* save, using the journal, and says "Finzytrack undid an interrupted save in *Personal*." Then *Business* opens and sees that `prices.beancount` is back to how it was before; its next save works on the right bytes. If the journal cannot be undone (say *Personal*'s files are on a disk that is unplugged), *Business* opens anyway, unless it uses one of the files the journal names: then it is refused with "prices.beancount has an unfinished save from *Personal* that could not be undone. Open *Personal* to fix it."

You approved this rule today, in place of the territory rule. Two invariant texts follow from it:

1. **INV-32 (new).** The scheduler design stops guarding against shared files because of this rule, so it carries data safety and deserves an invariant: CI then insists its tests exist before M2 can exit. The territory text applied earlier as INV-32 is removed; the ID stays reserved for this text.
2. **INV-30's exception.** INV-30 says opening, switching and closing never write a workspace folder, except "write-journal recovery on open". In the example, opening *Business* writes into *Personal*'s folder. ADR-0063 reads the exception as covering this: the write only puts back what *Personal*'s folder held before the cut-off save, the same write *Personal*'s own next open would make. But "on open" can also be read as "of the workspace being opened". A clearer sentence avoids that argument.

INV-31 needs no change: recovery runs only while no workspace is open (at start, and during a switch after the current one has closed), so "only the open workspace accepts requests or runs jobs" stays true. INV-28, INV-25, INV-27 and INV-29 are unchanged.

## Options
1. **Apply both texts below.**

   **INV-32 (new): Every unfinished save is recovered before any workspace opens.**
   - Rule: "Before any workspace opens (at start, and at each switch after the current workspace has closed), every write journal of the instance (in hosted mode, of the user container) is recovered by ADR-0012's rule. A journal names every file its save replaced or created by the canonical path resolved when the journal was written, and recovery writes only those paths, each only while it still resolves to itself. A journal that cannot be recovered blocks the open of every other workspace whose ledger set uses a file it names, and every copy that would read such a file, and nothing else (ADR-0063)."
   - Why: "With one open workspace, the only way one workspace can read or overwrite another's unfinished work is a save cut off by a crash. Undoing every such save first makes even a file used by two workspaces harmless: the next open sees the change and rebuilds (ADR-0063)."
   - Enforced by: "planned: at start and at a switch, a journal of a workspace other than the target is recovered before the target opens; a journal that cannot be recovered (its folder unreachable, foreign bytes) refuses with `RECOVERY_BLOCKED` only the workspaces and copies that use a file it names, and the others open; a file reached through a symlink made after the save is recovered at the journal's recorded path, and a path that no longer resolves to itself is not written; regression tests for the S0045 verifier's F1 (a cut-off save's created file matched by another workspace's glob) and F-W (a symlink made after open sends a created file into another workspace's folder); a workspace with an unresolved journal can be neither forgotten nor located elsewhere."
   - Status: "pending (BL-0103, by M2)".

   **INV-30, the exception sentence (new text):** "**Stated exception:** write-journal recovery, which runs before a workspace opens and may put any workspace's files back to their state before an interrupted save, with a one-line notice naming that workspace (review:D1 §13.1; ADR-0063)."
   (Today: "**Stated exception:** write-journal recovery on open, with a one-line notice (review:D1 §13.1).")

2. **Apply INV-32 only.** INV-30 keeps its words, and ADR-0063's reading of them stands. Cheaper, but the next reviewer may report the gap between "on open" and recovery into another folder.
3. **Apply neither.** ADR-0063 alone carries the rule; nothing in `check_invariants.py` insists that BL-0103's tests exist before M2 exits.

## Recommendation
Option 1. The rule protects your ledgers after a crash and the scheduler relies on it, so it should be an invariant with tests CI demands. The INV-30 sentence only says out loud what ADR-0063 already does; it adds no new kind of write.

## Answer (owner, verbatim, with date)
Owner, 2026-10-27, in session (relayed by the orchestrator): "I agreed with all your recommendations." The orchestrator's recommendations were option 1 with one change, "every other workspace" becomes "every workspace" in INV-32's rule (the journal's own workspace is blocked too), and acceptance of the implementer's two additions (recovery writes a recorded path only while it still resolves to itself; Locate waits for recovery).

On the S0045 verifier's round-3 finding F-1 (an unresolved journal had no way out), owner, 2026-10-27, in session (relayed by the orchestrator): "Yes, I agree with the keep the files as they are now way out." The proposal: a deliberate "Keep the files as they are now" action drops the unfinished save's journal and leaves the files untouched (logged, listing the files it releases); forget is no longer refused and keeps the journal in `local/`; any open or registration, "Open folder…" and Locate included, goes through the same check.

Applied in S0045 fix round 3 (IMP-0035):
- INV-30's exception sentence exactly as in option 1.
- INV-32 as in option 1, with two changes: "every other workspace" became "every workspace" (the first answer); and the "Enforced by" item "a workspace with an unresolved journal can be neither forgotten nor located elsewhere" became "a folder whose files an unresolved journal names, or which is that journal's workspace moved or copied, is blocked at every open, Open folder and Locate included; "Keep the files as they are now" resolves it, writing nothing to the ledger files; forget keeps the journal" (the second answer: forget is no longer refused, and the "Locate waits" addition became the same check every open goes through). Nothing else changed.

