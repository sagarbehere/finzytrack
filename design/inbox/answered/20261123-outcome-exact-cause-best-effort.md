# The outcome must be exact; its cause is best effort
- Asked: 2026-11-23 by the orchestrator, after the owner's question in session during S0149
- blocks: nothing (S0149 continues under the owner's "Option 2")
- Rubric item: a process change (PROCESS.md, the review budget's finding classes), which is the owner's
- ADRs checked: ADR-0068 D6 (rounds on one mechanism), ADR-0069 (Windows' best-effort replace), ADR-0070. None decides how a refusal's cause is classed; IMP-0068, IMP-0069 and IMP-0075 set today's rules.

## Context
The owner's direction, in their words: "have high integrity where possible, and best effort where not possible. When doing best effort, tell the user."

The owner asked, during S0149, whether that is really so complicated: "It feels like we've been spinning our wheels for days and weeks on tackling various file management edge cases."

What the journals show for S0144 to S0149 (2026-11-22 and 23):
- No reviewer found a lost, torn or overwritten byte.
- One finding was an integrity escape that mattered: S0149's V1, a false "Your ledger is unchanged". It was fixed in one round.
- Most of the rounds, CI runs and owner questions went to **which cause a refusal names, and which file**: S0148's "in use" (two extra runs, three owner questions), and S0149's offline naming (three fix rounds and two options notes).

Three things made the cause expensive:
1. Each named cause is a new write-outcome text, and so gets the verifier, the data-safety red team, a fresh round after every fix, and an options note from the second round on.
2. A wrong cause was classed (a), like a false "saved".
3. The orchestrator chose precise per-cause texts at the brief, where a generic honest text would have done.

## The change
1. **The outcome is exact (class (a), unchanged).** These keep today's full rigour:
   - what a write says happened: "saved", "not saved", "unchanged", "undone", "created", "copied", "loaded";
   - the state recovery leaves (writes paused, a journal kept open);
   - which bytes are on disk.
2. **The cause is best effort (class (d)).**
   - Why a write was refused, and which file or folder a refusal names as the cause, is told as well as is cheaply known.
   - The default is one generic honest text with the system's own message: for example "Your change was not saved. Windows could not read or write <file>: <the system's message>. Your ledger is unchanged." (or "…could not check whether your ledger changed").
   - A specific cause (in use, only in the cloud, a network drive) is added only where it is cheap and obvious.
   - A wrong, vague or stale cause is a class (d) finding: fixed in its round (tests or docs, or a small text change) or dropped with a reason. It never becomes a further fix round, an options note or a backlog row.
   - A cause text that itself claims an outcome ("Your ledger is unchanged") is judged as an outcome: rule 1.
3. **Best effort is told.** Where a write runs in a best-effort mode (Windows' replace, a network share), the user is told. Today that is once per open. A "don't tell me again" choice is a UI item for when the open screen exists (M2).
4. **At the brief:** a slice that adds a refusal uses the generic cause text unless the owner asks for a specific one, and says so in one line.

## Answer (owner, verbatim, with date)
Owner, 2026-11-23, in session, after the orchestrator explained the change: "Yes, write it up. I'm already accepting it as you have explained"

**Decision:** accepted as written above. It is in PROCESS.md §3 (the finding classes and the review budget) and IMP-0078. S0149's option 2 (owner: "Option 2.") is already under way and finishes as decided.
