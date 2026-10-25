# May Finzytrack open encrypted (`.gpg`) ledgers that Beancount can read?
- Asked: 2026-10-25 by agent, slice S0015-cut-rows-cite-adrs (verifier F1)
- blocks: nothing now; the M1a overlay-load slice (BL-0003, the parity corpus) needs the answer
- Rubric item: 5 (cutting a user-visible capability) and 4 (INV-03)
- ADRs checked: ADR-0010 (refuses *writes* to encrypted files; its desktop LoadPolicy "governs … encrypted files"), ADR-0008 (the overlay "handles encrypted includes"), ADR-0009 (`.gpg` files are not editable), ADR-0043 (release scope: not listed among the four deferrals). None decides that *reading* is cut, yet the capability map marks CAP-DATA-ENCRYPTED `cut` with the note "the overlay loader refuses encrypted files" (from review:10 D2).

## Context
Beancount can load a ledger file encrypted with GnuPG (for example `bank.beancount.gpg` included from `main.beancount`) by calling `gpg` to decrypt it in memory. v0.3 reads such ledgers as far as Beancount does. INV-03 says: if Beancount loads a ledger, Finzytrack handles it. The map says the opposite for this one feature, and no ADR backs the map.

## Options
1. **Read yes, write no** (recommended). Finzytrack opens and shows ledgers with encrypted files; any change that would touch an encrypted file is refused with a clear message ("this file is encrypted; edit it outside Finzytrack"), as ADR-0010 already says. The row becomes `planned` (M1a) with this limit. Matches INV-03 and the ADRs.
2. **Cut reading too**: a ledger that includes an encrypted file opens with an error. Needs an ADR and an INV-03 exception, because it narrows a Beancount feature.

## Recommendation
Option 1. It keeps INV-03 whole and costs little: Beancount does the decryption; Finzytrack only has to refuse writes to those files and never copy the decrypted text anywhere (mirror, caches, logs) beyond what reading needs. That last point (the decrypted text in the mirror database) is worth one sentence of your attention: the mirror would hold the decrypted entries, as it does for every other file.

## Answer (owner, verbatim, with date)
