# Your privacy denylist does not exist yet: create it, and should the scan refuse to run without it?
- Asked: 2026-10-25 by orchestrator/M0 gate red team (finding M1)
- blocks: nothing today; the M1b local tier (copies of your real ledgers on your machine) should not start until the denylist exists
- Rubric item: 6 (privacy posture) and "reviewing anonymized real exports" (the owner's); creating the file is something only you can do, because its contents are your real amounts and names
- ADRs checked: PROCESS.md §8 (the privacy gate names "the owner's amount/name denylist kept outside the repo (`$FT_PRIVACY_DENYLIST` or `~/.config/finzytrack/privacy-denylist.txt`)"); INV-23; review:F "the privacy denylist (deferred)" was recorded as an open owner item in session 2. No ADR decides what the scanner does when the file is missing.

## Context (plain language, a concrete example first)

The privacy scan that runs on every commit and push already refuses account numbers, PAN, IBAN, emails, API keys and home paths. Its last rule is meant to catch **your own** amounts and names, for example a test that accidentally contains "Rent paid 48,750.00 to Mr Sharma" copied from your ledger. That rule reads a list of your amounts and names from a file outside the repo, so the list itself is never published.

Today that file does not exist, and the scan says nothing about it: the rule silently checks nothing. A misspelt path in `FT_PRIVACY_DENYLIST` is also silent. Until M1b this matters little (agents never read your real ledgers), but M1b starts running the write contract on copies of your real ledgers on your machine (the local tier), and from then on a real amount could end up in a test or a journal by mistake.

## What I'd like you to do
1. Create `~/.config/finzytrack/privacy-denylist.txt` with one item per line: amounts you'd recognise as yours (rent, salary, large balances, in the forms you write them, e.g. `48750.00` and `48,750.00`), names (landlord, employer, family), and any account nicknames. Agents never read this file; only the scanner does.

## Options for the scanner
1. **Refuse to run without the denylist** (recommended): the commit and push scans exit with an error when the file is missing, unless `FT_PRIVACY_DENYLIST=none` is set on purpose (the GitHub CI run sets it, because it has no access to your file). A misspelt path is also an error.
2. **Warn but continue**: the scan prints a warning when the file is missing and still passes.
3. **Leave it as it is**.

## Recommendation
Create the file, and option 1. A safety rule that silently checks nothing is the kind of gap INV-22 ("safety checks fail closed") exists to prevent.

## Answer (owner, verbatim, with date)
