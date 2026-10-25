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
- Self-mutation (High, Medium): <plan summary; n tried / n killed; survivors → test added, or why equivalent>
- Population (if the slice claims to cover a whole set): <inventory used, built by behaviour; counts in / out>

## Verifier
(Summarise the report; do not transcribe it. One line per finding. A process change is described once, in `IMPROVEMENTS.md`, and the journal points to its IMP entry. ADR-0058.)
- Round <n>, head <sha>: <verdict>. Attacks: <count and a few words>.
- <ID> <severity>: <one line: what, and the fix or backlog ID>

## Golden changes
- <file>: <why the old expectation was wrong>; verifier verdict: …; finding ID: …

## Retro
- What the verifier found / what a cheaper gate could have found / where time went / orientation gaps
- Process cost: <agent passes, wall time>
```
