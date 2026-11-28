# Five simplifications from the M1b area review: which do you accept?
- Asked: 2026-11-28 by the implementer, S0162-m1b-area-review-triage
- blocks: nothing (every item is a removal or a narrowing; each waits for your answer and holds no slice)
- Rubric item: 4 (each one changes or narrows an accepted ADR: ADR-0068 D8, ADR-0014, ADR-0008 step 4, ADR-0009); proposal 3 needs no decision and is listed so you see it
- ADRs checked: ADR-0068 D8 (the background save's join), ADR-0014 (promotion, the confined save), ADR-0013 (the swap), ADR-0008 (the confirm, step 4), ADR-0009 (the edit primitives). Each one decides the mechanism the proposal would remove or narrow, so none of them settles it.

## Context (plain language, a concrete example first)
The fresh area review of M1b (2026-11-28, at 78f105fa) found no data-safety or wrong-number issue. Besides its findings (triaged in S0162, `design/journal/M1b-exit.md` "Area review"), it proposed five simplifications: code or rules that were built before anything needs them, or built twice. The policy behind most of them is one you set already: **a mechanism is built with its first caller** ("refuse first, ship soon", IMP-0075; ADR-0068's step back).

Example for proposal 1: the app will one day update prices by itself in the background (M8). For that, M1a built a "background save" and a way for a second price update to join a queued one (`JoinableWork`). Nothing calls either today, yet they are code we test, review and keep correct through every scheduler change.

## Options (cheapest first; PROCESS.md §5)
Each proposal is answered on its own: accept, or keep as is.

1. **Delete the background save and `JoinableWork` until BL-0177 (M8).** Remove `scheduler.submit_background_save`, `JoinableWork`, and `save.submit_save(background=True)` with their tests; BL-0177 builds them with the price update, its first caller. ADR-0068 D8 gets a dated note: the rule stands (a background save plans when it runs, a second one joins it), the code waits for its caller. Cost: one small slice now, a little more work in M8. Keep: nothing changes.
2. **Drop the sidecar's own path check in the promotion guard.** `app.workspaces.writes.promotion_refusal` checks that a test copy's planned files lie inside the copy's folder (`_where_written`, `is_inside`), before the engine checks the same thing again during the save (the confined save, engine-jobs §6.1, ADR-0014). The engine's check is the authority: it opens the folder and reaches each file through it, following no link. The sidecar's check gives an earlier refusal and nothing else. The other half of the guard stays: a workspace that is not a test copy is still refused by the sidecar. Note: S0161 tightened `is_inside` for `..` a day ago, so the review history of this check is recent. Cost: a small slice; for a file outside the copy the user is told `CHANGE_NOT_SAVED {outside_folder}` by the engine's save instead of the sidecar's early `UNPROMOTED_MUTATOR_ON_REAL_LEDGER`; nothing is written either way.
3. **(No decision needed.) One `place_bytes` for the writer and the restore.** The durable sequence (stage, sync, exclusive rename, swap, check what was displaced) is written twice. Factoring it changes no behaviour and no ADR; it is now BL-0314 (M3, before a third caller copies it). Tell me if you want it sooner.
4. **Fold BL-0268's per-run plan-id prefix into the built `PLAN_UNKNOWN`.** BL-0268 asked for plan ids with a per-run prefix so a confirm from an earlier run is told "check your ledger". S0117 round 3 already answers every id this run does not know with `PLAN_UNKNOWN` ("Check your ledger first: an earlier confirm may have saved this change"). The prefix would only tell an earlier run's id from one never issued, which changes nothing the user does. BL-0268 would keep only the confirm route's own rules (the resent confirm while the workspace warms, engine-jobs §11; the `Host` check, BL-0115), which move to the confirm route's brief. ADR-0008 step 4 gets a dated note.
5. **Narrow the intent table: `insert_meta` and `delete_meta` become a whole-entry `replace`.** Four primitives the table names are not built (BL-0312, BL-0313). Two of them, adding or removing one metadata line, can be written as replacing the whole entry with the same entry plus or minus that line: the contract already checks a replaced entry is exactly the intended one, and the merge keeps every other key as written (ADR-0009). Example: setting `bank-id: "1234"` on an account's `open` replaces that `open` line and its metadata block with the same block plus one line. Cost: the intent table's rows for `account.edit`, `account.rename` and six later mutators change their `edits` (rubric item 1 for a `decided` row, so it is yours), and ADR-0009 gets an amendment; BL-0313 then closes without code. The risk: a whole-entry replace re-prints the entry from its parse, so a comment or odd spacing inside that entry's metadata block may be lost unless the producer splices the line into the entry's own text (which is how it would be built).

## Recommendation
Accept 1, 4 and 5; accept 3 as tracked (BL-0314); keep 2 as is for now.
- **1:** built in S0069 (ADR-0068 D8 came from a High of the step-back area review; the owner accepted the interim, "OK (Recommended)", 2026-11-01); no fix round since, no caller until M8. Removing it is cheap and BL-0177 already describes how to build it.
- **2: keep.** The check costs little, and it has had many rounds (S0117: four rounds with the data-safety red team, its Highs on the engine side; S0131: two rounds for Windows roots; S0161: the `..` refusal). Those rounds were on the engine's confined save more than on this check, but deleting it right after a fix is churn. Revisit when CAP-WS-TEST-COPY (M3) lands, with the confirm route.
- **4:** no rounds of its own; it is a row only (S0117 round 2 red team RT2-F2 asked for it, round 3 built `PLAN_UNKNOWN`, which answers the same need).
- **5:** no rounds; the primitives were never built. It removes two of the four missing primitives before anyone builds them.

## Answer (owner, verbatim, with date)
Owner, 2026-11-28, in session (each proposal answered on its own):
- 1: "Accept (Recommended)"
- 2: "Keep for now (Recommended)" (revisit when CAP-WS-TEST-COPY lands in M3)
- 3: no decision needed (BL-0314, M3)
- 4: "Accept (Recommended)"
- 5: "Accept, splice only (Recommended)" (the option as put: accept, with the rule that the producer splices the line into the entry's own text, so comments and spacing are kept (I10), never re-printed; intent-table rows and ADR-0009 amended; BL-0313 closes without code)

Carried out by S0167 (4 and 5, docs and spec) and S0168 (1, the code removal).

