# Slice journal

One file per slice: `<slice-id>.md`, where the slice ID is `S<nnnn>-<short-slug>` (e.g. `S0007-intent-table`). The commit messages of the slice carry its ID. Lookup order for "how did this change?": capability map → ADR → journal → `git log`.

Template:

```
# S<nnnn>: <title>

## Brief
- Goal:
- Implements: ADR-…, spec …
- Capabilities: CAP-…
- Invariants touched: INV-…
- Risk class / lane: High | Medium | Low / full | specialist | light
- Acceptance tests: <IDs, new or existing>
- Expected doc impact: <user docs, generated docs, ADRs>
- Open questions: <none | inbox file>

## Decisions
- <decision> (agent | owner: "<verbatim quote>", date)

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test: <check → test>
- Self-mutation (checks guarding ledger data, finance, real-data privacy or product security; optional on tooling): <survivors → test added, or why equivalent>
- Population (if the slice claims to cover a whole set): <inventory used, built by behaviour; counts in / out>

## Verifier
(Summarise the report; do not transcribe it. One table row per finding; class (a) bytes or a false outcome, (b) a wrong number, (c) a gap in our own checks or tests, (d) docs or wording, (e) other (PROCESS.md §3 review budget; IMP-0069). A process change is described once, in `IMPROVEMENTS.md`, and the journal points to its IMP entry. ADR-0058.)
- Round <n>, head <sha>: <verdict>. Attacks: <count and a few words>.

| ID | Severity | Class | Disposition |
|---|---|---|---|
| <F1> | <High/Medium/Low> | <a-e> | <one line: what, and the fix, backlog ID or reason> |

## Golden changes
- <file>: <why the old expectation was wrong>; verifier verdict: …; finding ID: …

## Retro
- Build <n> min, fix <n> min, <n> verifier rounds, findings <n>H/<n>M/<n>L. Cheaper gate: <one sentence>. Process checks: <check> <n> catch / <n> false positive, for each one that fired (PROCESS.md §9; IMP-0073)
```
