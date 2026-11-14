# S0120: no size limit on a ledger file a write meets; a tip above 15 MB (M1b, BL-0112, slice 5b)

## Brief
- Goal: build the owner's decision of 2026-11-15 (ADR-0008 amendment of that date): a write meets no size limit of its own on a ledger file. Today a single file above about 16 MB cannot be written, because the preview's answer, which carries the file's whole new text, passes the protocol's 16 MiB line and the engine is stopped (S0119). After this slice:
  1. **The protocol's line limit is a 256 MiB guard** (`MAX_LINE_BYTES`, in the sidecar's `app.platform.engine_protocol` and the engine's `ledger_engine.protocol.messages`, one value in both, in the generated `contracts/engine-protocol.schema.json`).
  2. **The preview never stops the engine on size**: the engine checks its own preview answer's size before sending it, and an answer that would pass the guard is refused with a key of its own (e.g. `engine.result_too_large`), not counted as a stop. The sidecar maps it to a registry error whose words say the file is too large to edit (an outcome text, §11). The save's request keeps today's refusal before sending (`too_large`, nothing sent), now at the new guard.
  3. **The plan store is bounded in bytes** as well as by count and TTL (`app.platform.plans`): its bound was `max_plans` × the line limit, which would become about 1 GB. A byte budget of 128 MB over the plans' texts, the oldest let go first, and **the newest plan is always kept**, so a file of any size can be previewed and confirmed; a plan let go answers as today (`PLAN_EXPIRED`).
  4. **The tip**: a prepared plan that changes a file larger than 15 MB (15,000,000 bytes, its bytes now or the candidate's) carries that file in a `large_files` list (or an equivalent field), and the preview's text tells the user that a large file is written more slowly and that splitting it with `include` keeps Finzytrack fast. A short text for non-native readers, rendered in `design/generated/engine-job-texts.md` or the registry's generated reference, whichever owns preview texts. The threshold is one named constant.
- Implements: ADR-0008's amendment of 2026-11-15 (and its 2026-10-31 counting per job), ADR-0054 (the protocol), ADR-0011 and INV-10 (bounded memory: the plan store's budget), `design/specs/engine-jobs.md` §5.5 (the line limits), §6.1's "Limit (speed)", §11 (texts); BL-0112.
- Capabilities: CAP-DATA-WRITE-GATE (text only if it states the size limit).
- Invariants touched: INV-20 (reworded on 2026-11-15, already in this slice's commit: the 5 s target holds when no file the write touches is larger than 15 MB), INV-10 (the plan store's byte budget), INV-01 and INV-22 (**a large candidate text must arrive whole or not at all**: a cut, short or over-long line is never decoded as a plan or a save request; the existing checks of a cut reply stay and are tested at the new size), INV-23 (no path or text in a log line; the `large_files` list never reaches a log).
- Risk class / lane: High / full. Review budget row: **Hardening an existing write path** (a gate's input: the line guard, the plan store's bound): the verifier; the data-safety red team joins only on a class (a) finding; a fresh round after any product fix, in which only High and (a)/(b) findings are fixed.
- The bytes it protects: the candidate text of a large ledger file, from the engine's preview answer through the plan store to the save's request; it must reach the save whole and byte-identical, or the write must be refused. And the truth of the outcome when it cannot: a refusal that says the file is too large, never an engine stop or "saved".
- Mechanism questions (PROCESS.md §3 step 2):
  - **Which real setup needs this, and can a policy forbid the rest?** A ledger kept in one large file: a long-time user of v0.3's importer, whose five metadata lines per transaction put 54k transactions in about 21 MB. The owner chose not to forbid it (a size limit was the cheaper option, offered first: "Let's do limit + clean refusal, but with a raised limit", then "Would it make sense to allow arbitrarily large files ...").
  - **Has this mechanism or its family needed two or more fix rounds before?** The plan store and the confirm: S0117 needed five verifier rounds and four red-team runs (4 High, all on the confirm's save; the plan store's own rounds are in that journal). The protocol's line handling: S0035 and S0040 built it; S0119's verifier found the reply-side failure. So **if any finding comes in, the policy option goes to the owner before the first fix round** (the owner's instruction for M1b's slices; S0117's retro).
- Acceptance tests (tests first; each fails on the base commit):
  - The engine refuses a preview answer over the guard with its own key, writes nothing and is not counted as a stop. Tested by lowering the guard in the test, not by building a 256 MiB file. The sidecar maps it to the too-large text.
  - The sidecar's reading of a reply over the guard is still a violation, and a cut line is never decoded, at the new value (the existing tests re-pointed).
  - The save's request over the guard is refused before sending, nothing sent.
  - The plan store's byte budget: the oldest plans let go by bytes; the newest kept even alone over the budget; a let-go plan's confirm answers `PLAN_EXPIRED` and writes nothing.
  - The tip: a plan changing a file above the threshold lists it, and one at or under it does not. The threshold is measured on the file's bytes now and on the candidate's.
  - **End to end** (the real engine, a test copy, the test producer as in `tests/workspaces/test_writes_end_to_end.py`): a single ledger file of about 21 MB (S0119's recipe: `backend/scripts/measure_write.py --layout meta20 --write-layout`) is previewed, confirmed and saved, and the file's bytes equal the plan's text. If it is too slow for the test suite, mark it `slow` with the repo's existing convention, and say which.
  - **Measured** with `backend/scripts/measure_write.py` (3 runs, loads recorded): the 21.3 MB file (preview, save, rebuild, confirm to publish, peak memory), and one file of about 40-50 MB built by a stated recipe, generated only, so the extrapolation in the amendment (about 6 s at 25 MB, 11-12 s at 50 MB) is replaced by numbers.
- Expected doc impact:
  - ADR-0008: the 2026-11-15 amendment is already written in this slice's first commit; correct its extrapolated numbers with the measured ones.
  - `design/specs/engine-jobs.md`: §5.5 (the guard and the preview's refusal), §6.1 "Limit (speed)" (the 15 MB line and the large-file numbers), §11 (the two new texts).
  - `contracts/engine-protocol.schema.json` regenerated; the error registry and its generated reference.
  - BL-0112 closed with the owner's words; BL-0106's text (its line-limit sentence).
  - One-line pointers to the amendment where the 5 s absolute is restated: the roadmap's G1 criteria, PROCESS.md §6's "The only absolutes", ADR-0046's G1 criteria. A pointer only, no new rule.
- Open questions: none.
- Base: c9613f8b.

## Decisions
- No size limit on a ledger file a write meets; a tip above 15 MB; the 5 s target at 54k holds up to 15 MB per file (owner, 2026-11-15, in session; quoted in full in `design/inbox/answered/20261115-large-single-file.md` and ADR-0008's amendment of 2026-11-15): "Would it make sense to allow arbitrarily large files (i.e. no size limits) and let them take more than 5s? We also provide a friendly tip that they should split the files to less than X mb (15?). But we don't stop loading files.. if a user has a huge file, then it'll take time and memory to open.. that's just how it is. Would that be feasible without substantially changing the architecture?", then "Yes, write the amendment and brief it as 5b".
- INV-20 reworded with that decision (rubric item 4, the owner's words above).
- (agent) The guard stays a guard (256 MiB), not unlimited: it also protects the sidecar from a broken engine's endless output. The plan store's budget is 128 MB over the plans' texts, with the newest plan always kept. Both numbers are the orchestrator's, offered to the owner with the recommendation the owner accepted ("Yes, write the amendment ...").

## Hand-off (PROCESS.md §3 step 4; ADR-0057)

## Verifier

## Retro
