# A ledger at the top level of a FAT or exFAT stick: refuse saving there (as built in S0089 round 1), or accept the risk?
- Asked: 2026-11-09 by the implementer of S0089-recovery-volume-id (round 1)
- blocks: nothing (the refusal ships; it fails closed; this asks you to confirm it)
- Rubric item: 5 (scope: it stops saving in one place users may keep a ledger)
- ADRs checked: ADR-0012 and its amendment of 2026-11-08 with your "Drive ID + folder birth (Recommended)" (2026-11-09). That decision assumes the ledger sits in a folder, such as `Finance`, whose creation time tells two sticks apart. It does not say what happens when there is no such folder.

## Context (plain language, a concrete example first)
Example: you keep `main.beancount` directly on a USB stick formatted as FAT, not inside a folder. Since round 1, recovery tells "your stick" from "another stick at the same place" by three things: the stick's ID, its file-system type, and the creation time of the ledger's folder.

The problem: FAT and exFAT keep no creation time for the top level of the stick (macOS answers 0; I checked on disk images). And FAT's stick ID is made from a 32-bit number set when the stick is formatted, so two sticks formatted with the same number have the same ID (the red team's finding H1). For a ledger at the top level, two such sticks could not be told apart. If a save were cut off and you then plugged in the other stick, recovery could write your old ledger bytes onto it.

Round 1 refuses a save there before anything is written: "main.beancount is at the top level of its drive, where Finzytrack cannot tell this drive from another one formatted the same way, so it could not undo the change safely if saving were interrupted. Make a folder on the drive, move your ledger into it, and open it from there." Ledgers in a folder on the stick, and ledgers on APFS or HFS+ drives (their top level has a creation time), are not affected.

## Options
1. **Keep the refusal (as built).** It never writes on the wrong stick. The cost: a user with a ledger at the top of a FAT or exFAT stick must move it into a folder once.
2. **Allow it, and state the risk as a limit.** Saving works there. The cost: the same-ID twin stick case (rare: two sticks formatted with the same serial number, a save cut off, then the wrong stick plugged in) can overwrite the other stick's file.

## Recommendation
Option 1. It matches your rule for network shares ("fails closed, never writes on a wrong drive"), and moving the ledger into a folder is a one-time step with a clear message.

## Answer (owner, verbatim, with date)
