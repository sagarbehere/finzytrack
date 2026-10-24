---
name: escalate
description: Send a question that only the Finzytrack owner can decide to the owner inbox, park the blocked slice and continue with other work. Use when design/process/escalation-rubric.md says a choice is the owner's.
---

# Escalate

1. Check `design/process/escalation-rubric.md`: is this really the owner's call, and does no accepted ADR already cover it? If an ADR covers it, follow the ADR instead.
2. Write `design/inbox/<YYYYMMDD>-<short-slug>.md` from the template in `design/inbox/README.md`: the context in plain language with a concrete example first, 2–4 options, a recommendation, and a `blocks:` line naming the slices it holds.
3. Note the inbox file in the blocked slice's journal under "Open questions", park that slice, and continue with unblocked work.
4. When the owner answers, quote the answer verbatim with the date in the inbox file and the journal, record the decision (ADR if it is one), and move the file to `design/inbox/answered/`.
