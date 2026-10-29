# S0056: owner decisions of 2026-10-29: composables folder, Vue notes on four ADRs, trust before a ledger runs code

## Brief
- Goal: apply three owner answers of 2026-10-29. Owner, 2026-10-29, in session: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it." Earlier the same day, on how trust is stored: owner, 2026-10-29, in session: "Yes, that works" (`design/inbox/answered/20261028-untrusted-ledger-load.md`, section "How trust is stored").
  1. The frontend features' `hooks/` folder becomes `composables/` everywhere (the open items 1 and 2 of S0055); INV-12's text "domain modules, hooks or machines" becomes "domain modules, composables or machines".
  2. ADR-0034, ADR-0035, ADR-0037 and ADR-0050 get an appended note: the frontend is Vue 3 (ADR-0053), the Vue equivalents of ARCHITECTURE §3 and §10 apply; ADR-0050 item 11 (own `useECharts` composable) stays.
  3. Inbox F2 answered with option 1: on desktop, a ledger that runs code does not open until the user trusts it. ADR-0040 amended; a backlog row for M2's open flow; L11-02, L11-06, L11.M-1 point at it; ARCHITECTURE and `ledger_engine.ledger.policy`'s docstring stop saying the body waits for the answer.
- Implements: ADR-0053 (Vue), ADR-0040 (amended here), ADR-0036 (structure, folder name), ADR-0041 (trust prompt ships in v0.4.0).
- Capabilities: CAP-WS-TRUST (milestone M3 to M2, note, backlog and ADR fields; no status change).
- Invariants touched: INV-12 (rule wording, owner-approved; INVARIANTS.md's own rule: an ADR and an IMPROVEMENTS entry), INV-19 (docs do not drift).
- Risk class / lane: Medium (an invariant's wording, an ADR amendment on security posture, test-fixture renames; no product behaviour change) / light.
- Acceptance tests: `scripts/tests/test_check_retired_terms.py`: the new `hooks/ folder` term has a must-not-pass line, git and Claude hook paths are trap lines that must pass, and the real repository passes. Fails on the base commit (the term does not exist; ARCHITECTURE §3.1 names `hooks/`). The frontend unit tests (`boundaries.test.ts`, the planned integrity tests) run with the renamed paths.
- Expected doc impact: INVARIANTS.md (INV-12), ARCHITECTURE.md (§3.1 tree, §3.2 generator note, the LoadPolicy line of §2, the ADR map row), ENGINEERING.md (slice layout), backlog.md (L2-07, L2-11, L11-02, L11-06, L11.M-1, the new row, totals), capabilities.toml (+ regenerated capabilities.md), ADR-0034/0035/0036/0037/0040/0050 amendments, a new ADR for the INV-12 wording, decisions/README.md index, IMPROVEMENTS.md, specs/roadmap.md (M1a note, M2 and M3 Built lines), the inbox file moved to `answered/`, `engine/src/ledger_engine/ledger/policy.py` docstring. No user docs (no behaviour change).
- Open questions: none on the rubric; each is answered by the owner quotes above.

## Decisions
- The rename and INV-12's wording (owner: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it.", 2026-10-29). INVARIANTS.md's own rule asks every change for an ADR and an IMPROVEMENTS entry: ADR-0066 and IMP-0048. No `frontend/src/features/` folder exists yet, so the rename touches only the boundary fixtures, the two planned integrity tests and the documents.
- ADR-0036 also gets a dated note (agent): it decides the structure ("`features/*` hold headless hooks"), so it is the record the folder name amends; the four ADRs the brief names get the Vue note.
- ADR bodies and journals keep their "hooks" (append-only history); ARCHITECTURE §3.2's sentence about `@hey-api/openapi-ts` says "generated TanStack Query helpers" (the tool's output, framework-neutral), and Vue's "lifecycle hooks", vue-router's "leave hook" and openapi-typescript's `transform` hook stay (their own words).
- The retired-terms term `hooks/ folder` (agent; the owner's condition "only if it can be done without catching git/Claude hooks" holds): a `hooks/` segment that starts a path, or one under `features/`. `.githooks/`, `.claude/hooks/`, `core.hooksPath` and `scripts/tests/hook_pins/` pass (trap line). Prose "machines/hooks/views" is not caught (preceded by `/`, not under `features/`); it was fixed by hand in EP-11.
- "A plugin that evaluates non-literal config", made exact (agent, from Beancount 3.2.0's source): only `check_average_cost`, `check_commodity` and `commodity_attr` pass their config to `eval`; "plain literal" means what `ast.literal_eval` accepts. Beancount's other plugins never ask whatever their config: `currency_accounts` takes an account name, which is no Python literal and is never evaluated, so a blanket "non-literal config asks" would prompt for a harmless built-in. The trust key keeps the owner-agreed "whether each plugin's config is a plain literal".
- L11-02, L11-06 and L11.M-1 move from M1a to M2 with BL-0156 (owner: "No, ledger that runs code dosen not open before I trust it.", 2026-10-29, quoted on each row for the deferral check; the inbox's recommendation, which option 1 carries, says "Built in M2 with the open flow"). The quote does not name a milestone: the orchestrator should confirm the move with the owner if a milestone-specific quote is wanted. Leaving them at M1a would hold M1a's exit on M2 work.
- Observation, not acted on: on desktop the trust decision confines only code; includes and documents folders stay allowed. After BL-0156, L11-02 and L11-06 hold only hosted work (post-v0.4). Moving them to `post-v0.4` is a deferral for the owner; BL-0156's slice should raise it.
- CAP-WS-TRUST moves from M3 to M2 (earlier, with BL-0156), and roadmap M3's "the trust prompt" moves to M2's Built line (agent; scheduling only).
- The backlog's per-milestone line was recounted from the tables: M1a 5 (the old line said 9 where the tables held 8 before this slice), M2 50, total 226 (the "to build" row already said 226).
- The inbox answer quotes all three sentences of the owner's message, noting that the first two answer other questions.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `scripts/check_retired_terms.py`, new term `hooks/ folder` → `Terms.test_each_retired_term_is_refused` (`RETIRED_LINES["hooks/ folder"]`), `Terms.test_every_spelling_of_the_hooks_folder_is_refused` (six spellings, each alternative alone), `Terms.test_the_trap_words_pass` (git and Claude hook paths), `RealRepository.test_the_repository_passes`.
  - Tests first: on the base commit the module had 9 failures (session scratchpad `s0056/retired-tests-on-base.txt`); after the term, the real repository still failed on ARCHITECTURE §3.1's `hooks/` line until the docs changed; then all 34 pass.
  - No product check changed: `policy.py` changed only in its docstring; `frontend/eslint.boundaries.js` was not changed (it places files by `src/features/*`, whatever the sub-folder).
- Self-mutation (tooling; optional, run anyway): plan `s0056/mut.toml` in the session scratchpad, 4 mutants (drop the start-of-path alternative, drop the `features/` alternative, loosen the look-behind to `[\w-]`, drop the look-behind): 4 killed, 0 survived.
- Population: the rename, built by behaviour (every tracked file naming a `hooks` path or the word, `git grep -i hook`, minus git and Claude Code hooks, lockfiles and the generated `webhooks` type): frontend 3 files, 22 lines (boundaries.test.ts 17, integrity/transactions.test.ts 3, integrity/widgets.test.ts 2 plus one "hook" comment word); current-state docs 7 lines (ARCHITECTURE.md 4, ENGINEERING.md 1, INVARIANTS.md 1, backlog.md 2 titles L2-07, L2-11) and the check's own test line; history left: ADR bodies (0006, 0035, 0036, 0037, 0038, 0053, 0058, 0060), journals, IMPROVEMENTS.md.

## Verifier
- Not run yet; the orchestrator starts it on this slice's range.

## Retro
- Build 45 min, fix 0 min, 0 verifier rounds so far, findings 0H/0M/0L. Cheaper gate: none needed; the retired-terms check now holds the folder name.
