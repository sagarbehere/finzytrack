# How many old mirror copies may Finzytrack keep on disk during a burst of saves?
- Asked: 2026-11-08 by the S0085 implementer (the mirror publish design)
- blocks: S0086 (the cleanup rule only; the rest of the publish can be built meanwhile)
- Rubric item: 4 (it would change ADR-0015's amendment of 2026-10-25, confirmed on 2026-11-07)
- ADRs checked: ADR-0015 and its amendments (the grace period keeps every replaced copy for 10 minutes, with no limit on how many); ADR-0011 and INV-10 (bounded memory and caches: they bound memory, and say nothing on the cache's disk); none settles a limit on disk.

## Context (plain language, a concrete example first)

Example: you categorize transactions, saving one every 5 seconds for 10 minutes: 120 saves. After each save Finzytrack rebuilds the mirror (the SQLite copy the screens read) into a new file and switches the screens to it. The rule you confirmed keeps every replaced file for 10 minutes, so that a screen or an outside tool that is still reading the old file finds it. With a 54,000-entry ledger each file is about 80 MB, so those 10 minutes keep up to about 120 files, roughly 9.6 GB, in the app's cache folder. With a 10,000-entry ledger it is still about 1.8 GB. The files are deleted once their 10 minutes have passed, at the next save or open; nothing is lost if they go earlier.

On macOS and Linux, deleting a file that a reader has open is harmless: the reader keeps reading it. The 10 minutes only help a reader that looks the file up again by name: one screen request that reads several times (milliseconds to seconds), or an outside tool that reopens the file.

## Options
1. **Keep the rule as it is** (every replaced copy for 10 minutes). Simple; the disk use above is possible during long editing sessions.
2. **The 10 minutes, but at most 2 old copies besides the current one and the previous one** (the oldest goes first when there are more). Disk stays at about 4 files (about 320 MB at 54k). A screen request whose file went early answers "try again" and the screen reads again; an outside tool that reopens by name after more than 2 further saves finds its file gone and opens `current.db` again.
3. **No grace period: keep only the current and the previous copy.** Least disk (2 files); a request that spans two saves gets "try again" more often.

## Recommendation

Option 2. It keeps what the 10 minutes are for (a request in flight, a tool that just looked the file up) and bounds the disk to a few files whatever the editing pace. Nothing is lost in any option: the mirror is a copy that is rebuilt from the ledger.

## Answer (owner, verbatim, with date)
