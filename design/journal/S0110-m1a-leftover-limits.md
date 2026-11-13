# S0110: the leftover M1a rows closed as stated limits (BL-0225 F3, BL-0246, BL-0247)

## Brief
- Goal: close M1a's last three agent rows as the owner decided, each as a stated limit (the cheapest option offered).
- Implements: the owner's decision of 2026-11-13, in session, on the three options proposed after S0109: "Let's go with your recommendations for the three open questions." The recommendations were: BL-0225 F3, close as a stated limit in the model's mapping; BL-0246, a stated-limit line in the spec; BL-0247, a stated-limit line in spec §8.1.
- Capabilities: none changed.
- Invariants touched: none (no byte or outcome changes; INV-01, INV-22, INV-32 untouched).
- Risk class / lane: Low / light. Review budget row: docs only (verifier on facts and doc consistency, one round, no fresh round).
- Mechanism questions (§3 step 2): none; the slice adds or hardens no mechanism (it states the limits of existing ones).
- Acceptance tests: none (docs only); the test the BL-0225 F3 limit names already exists (`engine/tests/test_recover_ops.py::test_the_listing_removes_a_part_and_names_each_open_journal`).
- Expected doc impact: `formal/tla/engine-jobs/MAPPING.md` (the recover replay's "Limit (`.part` at boot)"); `design/specs/engine-jobs.md` §8.1 ("Limit (flush window)" in step 4; "Limit (keep read-only)" widened to the exFAT/FAT32 `EACCES` case; §15's index); `design/backlog.md` BL-0225, BL-0246, BL-0247 done with the owner's words.
- Open questions: none.
- Base: 484afd0f.

## Decisions
- All three as stated limits (owner: "Let's go with your recommendations for the three open questions.", 2026-11-13). Not chosen: BL-0225 F3's model change (a new boot state in the model, its worlds and the replay); BL-0246's wording fix and read-only detection; BL-0247's re-hash after the flush.

- BL-0219 deferred to M11 (owner, 2026-11-13: "Does my mac have a network share? If not, I want to defer this check"): `mount` showed no network share on the owner's Mac; the refusal for a share without a drive ID stands meanwhile. Added after the verifier's range began, in its own commit.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none.
- Self-mutation: not applicable.
- Population: not applicable.

## Verifier
- Round 1, head 3791e72a: pass with findings. Attacks: 8 (the model's boot states and the replay; `restore._flush`'s identity check; a read-only exFAT image probed end to end with `hdiutil`: Keep, a try at post, Retry after remounting writable; the backend text per outcome; §15's index; the rows' quotes; roadmap and capability drift). Docs-only row: one round, no fresh round.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| F1 | Low | d | Fixed: the widened "Limit (keep read-only)" now names the screens the verifier saw on a read-only exFAT image (Keep: the generic action-failed text; a try at post: stops `permission` with §11's permission row, as on any read-only mount; a try at pre: fails at the sync), and that Retry or Keep works once the drive is writable |
| F2 | Low | d | Fixed: roadmap §1's "Next" line, with the BL-0110 and BL-0109 outcome |
| F3 | Low | d | Fixed: §15's index in text order; two missing full stops on BL-0246 and BL-0247 |

## Retro
- Build 20 min, fix 15 min, 1 verifier round, findings 0H/0M/3L (all class d). Cheaper gate: probing the read-only exFAT case end to end before writing its limit, as the verifier did with `hdiutil`, would have caught F1. Process checks: invariants 0 catch / 0 false positive; deferrals 0 / 0.
