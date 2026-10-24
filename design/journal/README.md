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

## Verifier
- Head SHA verified:
- Attacks tried: <list; High ≥ 5, Medium ≥ 3>
- Findings: <ID, severity, resolution or backlog ID>

## Golden changes
- <file>: <why the old expectation was wrong>; verifier verdict: …; finding ID: …

## Retro
- What the verifier found / what a cheaper gate could have found / where time went / orientation gaps
- Process cost: <agent passes, wall time>
```
