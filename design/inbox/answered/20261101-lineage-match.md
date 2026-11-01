# Keep matching interrupted saves to a moved workspace folder by its lineage id, or drop it as ADR-0068 D7 says?
- Asked: 2026-11-01 by the orchestrator, from S0073 part A (the implementer parked the cut; the orchestrator checked the code)
- blocks: nothing (the lineage cut is parked as BL-0191; the other D7 cuts are built in S0073, and the spec describes the code as it is)
- Rubric item: 4 (carrying out, or not, a decision of ADR-0068 D7) and 1 (the journal format changes if it goes)
- ADRs checked: ADR-0068 D7 ("Records are matched by `path_key` only, not by lineage"; no scenario given); ADR-0063 decision 2 (an unrecoverable journal blocks the workspaces that use its files; INV-32); ADR-0012/ADR-0063 (the lineage match itself). The step-back proportionality review listed it as item 31 "lineage matching SIMPLIFY (path_key only)" with no harm stated, so the trade-off below was not in front of you when you agreed.

## Context (plain language, a concrete example first)
Each workspace has a lineage id, written in its folder's manifest, so it moves with the folder. An interrupted save's journal records the workspace's registry key, its lineage id and the files it touched.

Example. Your ledger is two files in folder A. A save changes both. The power fails between the two file swaps: `main.bean` has the new text, `inc/a.bean` still the old one (a half-applied save). Before you start Finzytrack again, you move the folder from A to B. At start, recovery cannot reach the files at A, so it cannot undo the save; the journal stays open. You then open B with "Open folder…": B gets a new registry key but the same lineage id.

- **Today (lineage match):** B does not open. You see the blocked screen: Retry, or "Keep the files as they are now". Saving to B is refused until then (`recovery.blocking_for`, `save.records`).
- **With `path_key` only (D7):** none of the journal's paths is in B's file list, so B opens without a word on the half-applied files, and you can save on top of them.

What it protects: **no bytes are lost either way**: the old bytes stay in the save's backup, and that journal and its backups are never trimmed (BL-0169 keeps every `.open` journal). It protects you from working on a half-applied two-file save without knowing it: it makes you see it and choose. Without it, that choice is made for you (a Keep you never confirmed), and INV-32's "blocks every workspace that uses its files" no longer holds for a moved folder.

Likelihood: rare. It needs a save that changes two or more files, a crash or power loss inside its swap window (milliseconds), and a folder move before the next start. A one-file save cannot be half-applied.

Cost of keeping it: it is built and tested. One field per record and two comparisons (in the open's block and in the save's gate). Dropping it costs a journal format bump (format 5; older journals unreadable, as S0070 did), code and test changes, and a sentence in the move help.

## Options
1. **Keep the lineage match** (reverse that line of D7). No code change; the spec and ADR-0068 get a note.
2. **Drop it, as D7 says**, and say in the help for moving a folder: "Move a workspace folder only while Finzytrack is closed and after it started cleanly once." Journal format 5.
3. **Drop it from the records but make "Open folder…" refuse a folder whose manifest lineage has an open journal.** The same check under another name; no simpler.

## Recommendation
Option 1. Keeping it costs nothing more, removing it costs more work than it saves, and it is what makes you see a half-applied save after a folder move instead of building on it silently. No byte is at stake either way, so this is your call on the policy, not a data-safety must.

## Answer (owner, verbatim, with date)
"I accept your recommendations for lineage match and drain fork race." (owner, 2026-11-01, in session)
