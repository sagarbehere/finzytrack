# Should Finzytrack refuse to save a ledger file that has a second hard link?
- Asked: 2026-10-29 by the orchestrator (S0060-durable-order, data-safety red team finding 1, High)
- blocks: S0060 landing (the High fix's last part); nothing else
- Rubric item: 5 (cuts what a user can do with their files) and 8 (both readings are plausible)
- ADRs checked: ADR-0012 and ADR-0013 (journal and atomic swap: say nothing about hard links), ADR-0062 and ADR-0063 (single instance, one open workspace; ADR-0063 mentions hard links only for the overlap check it rejected), ADR-0008 (write-safety contract). None decides hard-linked ledger files.

## Context (plain language, a concrete example first)
A hard link is a second name for the same file: `~/ledger/main.bean` and `~/Backup/copy.bean` can be one file on disk, so editing either changes both. It is rare, but some backup or sync setups make them.

The red team found, and I re-ran on this Mac, that the save then goes wrong. macOS reports a hard-linked file's path as "whichever name was looked up last". When another program (Spotlight, a sync or backup tool) touches the other name, Finzytrack writes the new bytes at `~/Backup/copy.bean`, leaves `~/ledger/main.bean` unchanged, and says "saved". The repro hit on the first try.

Part of the fix is needed whatever you decide, and will be built: find a file's path through its folder plus its name, and check it is the same file we opened. Then a second question remains. Finzytrack saves by writing a new file and swapping it in, so that a crash never leaves a half-written ledger. A swap gives the name a new file, so the hard link is broken: after the save `main.bean` has the new bytes and `copy.bean` still has the old ones, without anyone being told.

## Options
1. **Refuse to save a ledger file that has more than one hard link.** The message names the file and says to remove the extra link (or copy the file) first. Reading and reports still work. This is simple and safe, and nothing surprising happens.
2. **Save, and break the link with a warning.** The save lands, and the user is told that `copy.bean` (if we can find it) no longer follows `main.bean`.
3. **Save in place to keep the link** (overwrite the file's bytes instead of swapping). This loses the crash safety of the swap, because a crash mid-write tears the ledger (INV-01). Not recommended.

## Recommendation
Option 1, in the spirit of "simplify by policy": forbid the rare setup rather than build defences for it. It fails closed, it is easy to explain, and it can be lifted later (option 2) if someone needs it. The check runs before the save writes anything (a file's link count, `st_nlink`), so a refused save changes nothing.

## Answer (owner, verbatim, with date)
Owner, 2026-10-29 (in session, relayed by the orchestrator): "Refuse to save it (Recommended)". The option as shown: "Before writing anything, a ledger file with more than one hard link refuses the save with a message naming the file: remove the extra link or copy the file first. Reads and reports still work. Simple, fails closed, can be lifted later."

Carried out in S0060's round-1 fix commit: `ledger_engine.ledger.writer._hard_links`, the ADR-0013 amendment of 2026-10-29, `design/specs/engine-jobs.md` §6.3 and §15.
