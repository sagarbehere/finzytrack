# Amend ADR-0015: the ledger fingerprint is a content hash, not (path, mtime, size)
- Asked: 2026-10-28 by the orchestrator, from S0051's verifier (round 1 F2, round 2 doc check)
- blocks: nothing built; it keeps two accepted records from contradicting each other (INV-19). S0051 is built on the content hash.
- Rubric item: 4 (changing an accepted ADR)
- ADRs checked: ADR-0015 (mirror; "`(path, mtime, size)` of every glob-expanded include"), ADR-0064 (header "Unchanged: … ADR-0015"; its spec `design/specs/engine-jobs.md` §1 defines the fingerprint as SHA-256 per file), ADR-0008 step 4 (the fingerprint covers the files and the absent paths the ledger names; says nothing about how files are hashed), ADR-0013.

## Context (plain language)
The fingerprint answers "has anything the ledger reads changed since we loaded it?" It decides whether the mirror is fresh and whether a save may go ahead. ADR-0015 says it is each file's path, modification time and size. The engine-jobs spec you accepted with ADR-0064 says it is a SHA-256 of each file's bytes, and S0051 built that. ADR-0064 also says ADR-0015 is unchanged, so the two records now disagree.

The content hash is the stronger choice: an edit that keeps the size and puts the timestamp back is caught, and a mere `touch` (or a sync tool rewriting the same bytes) no longer forces a rebuild. Measured cost at 54k entries: about 3 ms per check.

## Options
1. **Amend ADR-0015** (one appended amendment): the fingerprint is, per file the load reads, a SHA-256 of its bytes or its state (absent, or unreadable with its error); per `include` (a plain path too, which the loader also globs), the glob's match list in the loader's order; per documents folder, its listing with each name's presence; the presence of the file of each `document` entry Beancount checks; as `design/specs/engine-jobs.md` §1 defines it.
2. **Go back to `(path, mtime, size)`** as ADR-0015 says: cheaper on huge ledgers, but misses a same-size edit with the time restored, and rebuilds on every touch.

## Recommendation
Option 1. It records what is built and what the engine-jobs spec already requires, and it is the safer check.

## Answer (owner, verbatim, with date)
