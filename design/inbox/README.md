# Owner inbox

Escalations waiting for the owner (`../process/escalation-rubric.md`). One file per question, named `<YYYYMMDD>-<short-slug>.md`. When answered, the agent records the decision (an ADR, or the journal), quotes the owner's answer verbatim, and moves the file to `answered/`.

Template:

```
# <question, one line>
- Asked: YYYY-MM-DD by <agent/slice>
- blocks: <slice IDs, or "nothing">
- Rubric item: <1-8>
- ADRs checked: <IDs searched, and why none settles the question>

## Context (plain language, a concrete example first)
## Options
1. ...
2. ...
## Recommendation
## Answer (owner, verbatim, with date)
```
