# ADR-0045: Agents run locally; CI runs deterministic gates only; direct commits on `next`

This record decides where agent work runs, what CI does, how work lands, and how owner approvals are recorded.

- **Status:** Accepted. Amended by ADR-0058: the T1 verifier-SHA check is parked (BL-0034); the verifier checks the SHA meanwhile. Amended by ADR-0060: GitHub CI runs when started by hand, before each release; the T0 pre-push hook is the per-push gate.
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** process, CI, repository

## Context

The process relies on agents (implementer, verifier, red teams, auditors, reflection). The final review proposed an automatic reviewer on GitHub, audits by another vendor's model, and a pull request per change with a separate agent account. The owner does not want AI spend beyond the existing Claude Code setup.

## Decision

(owner, 2026-10-24)
- **Every agent run happens locally in Claude Code on the owner's machine:** implementer, verifier, doc and drift auditors, red teams, area and lens reviews, reflection, spot audits and exploratory UI sessions.
- **CI (GitHub Actions) runs deterministic gates only.** No paid CI AI, no paid external review API, no GitHub-hosted agent runs, no automatic AI reviewer.
- **Spot audits** by a fresh Claude context with a different prompt and role (briefed only with the invariants and the capability row), plus the **seeded-defect catch rate**, replace other-vendor audits. They run at G1, G2, after M8 and at M11.
- **Direct commits:** the agent develops on `next`, commits directly and pushes. No pull requests, no squash-merge workflow, no separate agent GitHub account, no CODEOWNERS file. `next` is unprotected; the T0/T1 gates and the slice loop are the guard. `main` is protected against force-push and deletion only, if that is cheap to set up.
- **Owner approvals** (clearances, risky changes, changes to rules or checks) are given in the Claude Code session. The agent records them in the slice journal (or `IMPROVEMENTS.md` for a process change) with the owner's confirmation **quoted verbatim**, the date and the commit it covers. An approval line without a quote is a T1 failure for the change classes that need one.
- **CI secrets** are the updater key and its password only; there is no AI key in CI.

## Alternatives considered and rejected

- **An automatic AI reviewer on GitHub for risky changes.** Rejected: AI cost per change.
- **Audits by another vendor's model.** Dropped for cost; the shared-model blind spot remains and is stated in `PROCESS.md`.
- **A pull request per change with an agent account and CODEOWNERS.** Rejected: ceremony without benefit for a single-owner project; GitHub does not let an owner approve a pull request opened under the same account anyway.

## Consequences

- "Per PR" in older planning text means "per push to `next`".
- Verifier reports carry the head SHA they verified; a T1 check confirms it matches the commit or its parent.

## References

review:F §2 items 6 (e)–(f), 7; review:20 §4, §4b, §5, §9; review:F §5 item 5; ADR-0001, ADR-0044.
