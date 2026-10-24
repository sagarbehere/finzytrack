# Escalation rubric

What an agent decides alone and what goes to the owner. Included by reference from `PROCESS.md` §5 and every agent definition; this file is the only copy. Changing it needs the owner and an ADR.

## Agents decide alone, and log the decision (journal or ADR)

Implementation structure, naming, library choice within the decided stack (`ARCHITECTURE.md` pins), test design, UI detail inside the design system, performance trade-offs that keep the user-facing absolutes, and raising a performance or memory baseline, a threshold or a cache-bound default (one-line reason in `IMPROVEMENTS.md`).

## Escalate to the owner, with a recommendation in plain language

Only when **no accepted ADR already covers the change** (a slice that implements an accepted ADR as written does not escalate; the verifier checks conformance). Items 4 and the list below the numbered items always go to the owner, ADR or not:

1. Anything that changes **what is written to the ledger** (format, metadata keys, directive choice) or **how existing ledgers are interpreted**.
2. **Finance semantics** a user sees: what a number means, sign or period conventions, gains, booking.
3. **Irreversible or migrating** operations on user data or config.
4. **Changing an invariant or an ADR.**
5. **Scope:** adding or cutting a user-visible capability.
6. **Security or privacy posture:** new data egress, authentication, a dependency with system access.
7. **Implementer–verifier disagreement** on a High item.
8. Anything **truly ambiguous**, where both readings are plausible and costly to reverse.

Always the owner's: raising the 30 s T0 budget or the per-change process-cost ceiling; loosening any gate or narrowing the verifier's scope; any change to an invariant (adding, tightening, weakening); a broken user-facing absolute (a write over 1 s at demo size or over 5 s at 54k entries); clearance sign-off per kind of edit; the G1 go/no-go; approving changes to `PROCESS.md` (agents may apply tightenings first); reviewing anonymized real exports before they are committed; opening the first-push gate.

## How

Write `design/inbox/<YYYYMMDD>-<short-slug>.md` from the template in `design/inbox/README.md`, with a `blocks:` line. Park the blocked slice and continue with other work. Batch questions. Explain finance concepts plainly, with a concrete example before any jargon: the owner is a software person, not a finance specialist.
