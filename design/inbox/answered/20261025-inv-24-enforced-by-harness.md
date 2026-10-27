# May INV-24's "Enforced by" line list the new acceptance-harness and local-tier guards?
- Asked: 2026-10-25 by the implementer of S0030 (acceptance harness)
- blocks: nothing
- Rubric item: 4 (changing an invariant: the text of `design/INVARIANTS.md` is yours, even when only the list of checks changes; precedent IMP-0007)
- ADRs checked: ADR-0001 (the process), ADR-0057 and ADR-0058 (hand-off and verification), ADR-0060 (CI). None covers editing an invariant's "Enforced by" line, so the rubric's "always the owner's" applies.

## Context (plain language, a concrete example first)
INV-24 says agents and tests never touch your real data or your running app. Its "Enforced by" line lists the checks that make this true, and today it ends with "Planned: the acceptance harness port and data-dir checks; the local-tier path guard."

S0030 built those planned checks. For example, when a browser test runs, it now gets its own backend on a port the operating system picks. If anything in that test tries to reach port 8001 (your app) or any other address that is not the test's own, the request is blocked and the test fails. Separately, a run that reads copies of your real ledgers (the "local tier") now accepts them only from one folder that you name in an environment variable, outside the repo.

I first edited the line myself; the verifier pointed out that this is your decision, so the line is back to its old text. Nothing waits on this: the checks run either way. The only effect is that the invariant's own list stays out of date ("Planned: …") until you decide.

## Options
1. Replace the line with this accurate list (only checks that exist and run; no item twice):

   `.claude/hooks/guard_paths.py`, `.claude/hooks/guard_bash.py`, `scripts/tests/test_guards.py`, .claude/settings.json (deny rules), `scripts/oracle/launch_legacy.py` (refuses the live data folder), `scripts/oracle/copies.py` (the copy guards, compared by folder identity and ignoring case; the local-tier guard: real-ledger copies only from the folder named by `FT_LOCAL_TIER_DIR`), `scripts/tests/test_oracle_copies.py`, `scripts/oracle/tests/test_harness.py` (copy containment, source unchanged, `--ledger-dir` only from repo corpora or the local tier), `scripts/acceptance/launcher.py` (the acceptance harness: its backend gets port 0, never the owner's port, and only folders the harness made in its own temp root; only processes it started are stopped), `scripts/tests/test_acceptance_harness.py`, `frontend/e2e/harness/fixtures.ts` (in browser tests, requests from the test's browser contexts reach only the preview server and the run's own backend; WebSockets are refused), `frontend/e2e/harness.spec.ts`, `frontend/tests/unit/e2eHarness.test.ts`.

   What it does not claim: requests that test code sends itself with `page.request`, `context.request` or plain `fetch` are not checked yet (backlog BL-0071).
2. Keep the current line. The checks still run, but the invariant keeps saying they are "planned".

## Recommendation
Option 1. It replaces "planned" with what is actually built, names each check once, and says plainly what is not covered yet.

## Answer (owner, verbatim, with date)
Owner: "Also, regarding the inbox questions: Yes to correcting the wording in the ADR sentences, Replace the line with the accurate list re: inv-24, defer the privacy list for now, and if it still matters, I'd pick the "slots are engine processes, queue lives in the sidecar" design and reword INV-28 and INV-31 and add INV-32 as proposed." (2026-10-27, in session)

Applied: option 1 in `design/INVARIANTS.md` INV-24, with an approval note (IMP-0036, 2026-10-27).
