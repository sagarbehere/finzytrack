# How Finzytrack is developed

The single current reference for the development process. **Owner:** the project owner. Agents propose changes; tightenings may be applied by agents and are approved afterwards; loosenings need the owner first (§9). Founding proposal: review:20 (frozen); adopted by ADR-0001. Last change: 2026-10-24, seeded at `next` setup (journal `design/journal/S0000-next-setup.md`). Latest improvement entries: `IMPROVEMENTS.md`.

Agent prompts link to sections of this file instead of restating them.

## 1. Goals

- Catch a defect **in the change that introduces it**, not in a big review weeks later.
- The owner writes a **small set of invariants** (`../INVARIANTS.md`) and answers **high-stakes questions** (§5). Agents decide everything else, and an independent agent verifies it.
- Tests are comprehensive and mostly executable. Docs, dev and user, do not drift from the code (§7).
- The process costs little per change; most of its cost is one-time setup, run by agents.

Non-goals: exhaustive upfront requirements; heavyweight traceability matrices (IDs in tests and docs are enough).

## 2. Where things live

| Path | What |
|---|---|
| `design/README.md` | Start here: reading order |
| `design/INVARIANTS.md` | Owner-owned rules with their enforcement and status |
| `design/ENGINEERING.md` | Engineering principles EP-nn and the verifier checklist |
| `design/ARCHITECTURE.md` | The target system, package map, API, flows, pins |
| `design/capabilities.toml` → `capabilities.md` | "Did we do X?": every capability with status, milestone, release-blocking flag (generated) |
| `design/decisions/` | ADRs, append-only: "why did we do Y?" |
| `design/specs/` | Per-area specs; each opens with an Orientation block. Behaviour lives in tests |
| `design/backlog.md` | The tracked work list; every TODO in code cites an ID here |
| `design/journal/` | One file per slice: brief, decisions, verifier summary, retro |
| `design/inbox/` | Escalations waiting for the owner (§5) |
| `design/process/` | This file, the escalation rubric, the improvement log |
| `design/generated/` | Generated references; never hand-edited (INV-19) |

Project knowledge lives in `design/`, versioned with the code, never only in an agent's private memory or a chat. References of the form `review:<doc> §<section>` point into the owner's private review archive and are evidence, not instructions.

## 3. The slice loop

A **slice** is the smallest change that delivers one verifiable behaviour (hours to two days of agent work).

1. **Orient.** Run the `orient` skill on the request: relevant capability rows, ADRs (with rejected alternatives), specs, code entry points, tests, open backlog items.
2. **Brief**: the first section of `design/journal/<slice-id>.md` (template in `design/journal/README.md`): goal; ADRs and specs it implements; invariants touched; acceptance tests; expected doc impact; risk class and lane; open questions. A question that hits the rubric (§5) goes to the inbox **before** coding.
3. **Tests first.** Write the acceptance and golden tests and see them fail. For behaviour v0.3.0 already has, write characterization tests against the legacy app first (`scripts/oracle/launch_legacy.py`, copies only). The T1 check `tests-fail-on-base` enforces this: new or changed tests must fail on the slice's base commit; tests tagged `@characterization` or `@regression-guard` are exempt. For UI whose shape is unknown: **spike, then pin** its behaviour in tests before the slice is done.
4. **Implement** under the gates (§6).
5. **Verify** with the independent verifier (§4).
6. **Docs** in the same change: generated docs regenerated, user docs updated on the docs repo's `v0.4` branch (light lane: at milestone end), ADRs for decisions made, the capability row updated.
7. **Land**: commit directly on `next` (no pull requests) when every gate is green and the verifier reports no unresolved High finding. Medium findings are fixed or get a backlog ID; Low findings may be dropped with a one-line reason. The commit message carries the slice ID. The journal records the verifier's head SHA; a T1 check confirms it is the commit or its parent.

**Definition of done:** gates green; verifier signed off; every touched invariant has a test that would fail if it broke; `ENGINEERING.md` followed or the deviation recorded; docs updated; no TODO without a backlog ID.

### Risk classes and lanes

| Class | Typical slices | Ceremony |
|---|---|---|
| High | Ledger writes, finance semantics, security, migrations, rubric items 1–3 | Full brief; tests first; verifier with neighbourhood context; doc check; red team (§4); capability row; journal and retro; UI smoke session if UI is touched |
| Medium | New behaviour in one area with no ledger, finance or security impact | Short brief; tests first; verifier on the diff plus touched callers; doc check; journal line; smoke session if UI is touched |
| Low | Docs, UI copy, refactors with no behaviour change, test-only changes | Brief of a few lines; verifier on the diff; gates |

The verifier may raise a slice's class, never lower it. **Lanes** (owner, 2026-10-24): the **full lane** for anything touching ledger writes; the **specialist lane** for finance and security work, whose specialist red team runs once per area unless a change alters what a number means or adds a new input; the **light lane** for read-only screens and catalogue entries (user docs at milestone end).

**Ceilings only the owner raises:** the 30 s T0 pre-commit budget and the per-change process-cost ceiling (agent passes and wall time per slice, measured per §9).

## 4. Agents and reviews

All agent runs happen **locally in Claude Code on the owner's machine**. CI runs deterministic gates only; nothing in the process uses paid CI AI or hosted agent runs (owner, 2026-10-24).

| Role | Sees | Produces |
|---|---|---|
| Implementer (`.claude/agents/implementer.md`) | Brief, ADRs, specs, code | Code, tests, docs, the journal |
| Verifier (`.claude/agents/verifier.md`, fresh context, adversarial, doc check folded in) | The diff, the brief, `INVARIANTS.md`, `ENGINEERING.md`, the relevant ADRs, the slice's neighbourhood. **Never the implementer's reasoning** | Findings with repros, citing INV/EP/ADR/CAP/SPEC/BUG IDs; the list of attacks tried (High ≥ 5, Medium ≥ 3); the head SHA |

Rules: the verifier tries to **break** the change; executable evidence beats argument; implementer–verifier disagreement on a High item is escalated, never settled by the implementer. Red teams (data safety, finance correctness, security, UX), the area reviewer, the drift auditor and the reflection agent are added when first needed, with these decided cadences: data-safety red team on each write slice; finance and security red teams once per area and for changes that alter what a number means or add a new input; the UX red team before each vertical is declared at parity; an area review every 5–8 slices and before parity; the drift auditor weekly (locally); seeded-defect plants at G1, G2, after M8 and at M11 (≥ 20 plants across ≥ 4 classes).

**Review altitudes:** (1) the slice verifier, every slice; (2) an area review when a vertical reaches a milestone; (3) cross-cutting lens reviews at G1, G2, after M8 and at M11 only; (4) a whole-system review at M11. Findings from altitudes 2–4 are capped per run and triaged: integrity, security or wrong-number findings become slices at once; the rest join the backlog. Spot audits by a fresh Claude context with a different prompt and role run at G1, G2, after M8 and M11, alongside the seeded-defect catch rate.

**UI testing:** scripted Playwright acceptance flows (Chromium and WebKit) are the backbone; exploratory agent sessions with charters find what scripts miss, and every real issue they find becomes a Playwright test. Sessions use an isolated backend, a fresh demo workspace and the LLM stub, never the owner's browser profile or real ledgers. The packaged macOS UI cannot be driven (`tauri-driver` has no macOS support): macOS uses `--smoke-test`, sidecar and API checks, Playwright WebKit against the sidecar-served bundle, and the owner's dogfooding.

## 5. What reaches the owner

The rubric is one file: [`escalation-rubric.md`](escalation-rubric.md). Agents and this document both include it by reference.

Every escalation is a file `design/inbox/<id>.md` with the question, the options, a recommendation in plain language and a **`blocks:`** line naming the slices it holds. A blocked slice parks; the agent continues with unblocked work. Escalations are batched. Whether an accepted ADR already covers a change is judged by the verifier.

**Owner approvals** (clearances, risky changes, changes to rules or checks) are given in the Claude Code session. The agent records each in the slice journal, or in `IMPROVEMENTS.md` for a process change, with the owner's words **quoted verbatim**, the date and the commit it covers. An approval line without a quote fails T1 for the change classes that need one.

**Owner-reported bugs:** present the diagnosis (root cause, evidence, proposed fix) and get agreement before editing. This applies to bugs the owner reports, not to defects agents find inside the slice loop.

## 6. Gates

| Tier | When | Contents | Budget |
|---|---|---|---|
| T0 pre-commit | Every commit (`.githooks/pre-commit`) | Staged files only: staged-file guard, privacy scan, generated headers, format and lint of staged files | ≤ 30 s (owner raises) |
| T0 pre-push / Stop hook | Every push (`.githooks/pre-push`); end of an agent session (`.claude/hooks/stop_checks.py`) | Owner push gate, check-script meta-tests, privacy scan of all tracked files, invariants, capability map, generated artifacts; types and changed-area unit tests once the packages exist | ≤ 2 min |
| T1 | Every push to `next` (`.github/workflows/ci.yml`) | All unit and property tests; `tests-fail-on-base`; verifier-SHA and approval-quote checks; licence checks and `THIRD_PARTY_NOTICES` current; golden corpora; architecture tests; invariant and capability checks; concept counters; doc checks; a risk-selected acceptance subset (flows tagged with the touched capabilities and invariants, plus a fixed integrity core) | ≤ 10–15 min, sharded |
| T2 | Nightly and weekly (deterministic) | The full acceptance suite in Chromium and WebKit; 54k perf and memory against the baseline; D1 corpus and oracle comparison at full size; packaged builds on three OSes; docs link check. Weekly and before each clearance: mutation testing and the scripted UI tour | Off the critical path |
| T-local | Owner's machine, before promotions and release | The write corpus, oracle and overlay equivalence on copies of the owner's real ledgers (outside the repo) | Off the critical path |

The gates arrive with the code they check. **Built today:** the T0 hooks, and in T1 the check-script meta-tests, privacy scan, staged-file guard, invariant, capability, generated-artifact and licence checks. **Not built yet** (tracked): `tests-fail-on-base`, the verifier-SHA, approval-quote and golden-justification checks (BL-0034), the per-package jobs (BL-0037), the §10b no-drift checks (BL-0038). **Install the hooks once per clone:** `git config core.hooksPath .githooks`.

**Safety outranks every number.** No performance target, budget or baseline is ever met by weakening, skipping, sampling or deferring past its guarantee a safety check (the write-safety contract and its re-check, INV-01, INV-22, the privacy and real-data guards). An agent that cannot meet a user-facing absolute escalates with the measured numbers. **Thresholds catch accidents.** Performance and memory checks detect regressions against the previous baseline (for example > 20%), not absolute targets. The only absolutes are the owner's user-facing ones: a write takes ≤ 1 s at demo size and ≤ 5 s at 54k entries. Raising a threshold, baseline or cache-bound default is agent-approved with a one-line reason in `IMPROVEMENTS.md`, except the two owner-only ceilings (§3). Size thresholds **report** and never fail CI. Structural checks that fail CI: duplicate-code detection, layering contracts, the globals allowlist, concept counters (each kernel concept has one allowed home). No transitional allowlist: copied v0.3 code meets every gate on arrival.

## 7. Docs that cannot drift

Generate it, test it, or keep it to the "why".
1. **Generated:** API reference and error codes, mirror tables and views, the recipe schema and catalogs, the config reference, the capability map, the invariant index. CI regenerates them and fails on any diff (`scripts/check_generated.py`).
2. **Tested examples:** every recipe, SQL, BQL and ledger snippet in the user docs runs in CI; screenshots come from the Playwright suite.
3. **Symbol references:** specs and ADRs cite code as `` `module.symbol` ``; a checker fails on a missing symbol.
4. **Doc impact:** each brief lists its expected doc changes; the verifier compares them with the diff.
5. **ADRs are append-only:** a new decision supersedes an old one explicitly.
6. **User docs** (`finzytrack-docs`) change on its `v0.4` branch in the same slice (light lane: at milestone end).

**Project memory.** The capability map answers "did we do X?" (cut and deferred rows stay, with their ADR). ADRs answer "why?". The journal answers "when and how?". Look up in that order, then `git log`. Tests carry capability and invariant tags (`@pytest.mark.cap("CAP-…")`, `describe('[CAP-…]')`). CI checks that every `built` capability has a tagged test.

## 8. Tests, goldens and privacy

- **Oracles before opinions:** Beancount itself, `bean-query`/Fava outputs, the legacy app on copies, and hand-checked fixtures for app-specific numbers.
- **Pyramid:** pure unit and property tests (Hypothesis, fast-check); golden corpora; API contract tests; Playwright acceptance flows. Mutation testing weekly and before each clearance.
- **Changing an expected output** (golden, snapshot, characterization assertion) needs a justification line in the journal, a verifier verdict on old vs new side by side, and for a deliberate fix of known-wrong behaviour, the finding ID. T1 fails otherwise.
- **Privacy gate (INV-23).** The T0 scanner (`scripts/privacy_scan.py`) runs on every staged file: card and account numbers, PAN, IBAN, emails, API keys and tokens, home paths, and the owner's amount/name denylist kept **outside** the repo (`$FT_PRIVACY_DENYLIST` or `~/.config/finzytrack/privacy-denylist.txt`). The staged-file guard (`scripts/check_staged.py`) refuses ledgers, env files, mirror databases and app config outside `corpora/` and `fixtures/`, and refuses bank/broker exports unless listed in `corpora/MANIFEST.tsv` with their SHA-256 and the owner's review date. Real exports enter the repo only anonymized, and only after the owner reviewed the result. Security repros that work against a released version stay in the private archive until a fixed version ships; on `next` a fix lands with a neutral regression test.
- **Agents and real data (INV-24).** Agents never read the owner's live data folder, never write into the legacy worktree, never stop the owner's running app, never run `git clean -x/-X`. `.claude/settings.json` and the guard hooks enforce the common cases. Tests run on copies in isolated data dirs and ports. Real-ledger copies run only in T-local, from a folder outside the repo given by an environment variable, and its output prints counts and case IDs only.

## 9. Reflection and self-improvement

- **Every escape becomes a gate.** An escape is a defect found later than it should have been. Its analysis names the gate that should have caught it and the root-cause class; the fix includes the prevention. The same class must not escape twice.
- **Retro on every slice** (a few lines in the journal): what the verifier found, what a cheaper gate could have found, where time went, any orientation gap.
- **Periodic reflection** (about every 5 slices and at milestone boundaries) proposes process changes with a hypothesis and a metric: escapes, verifier findings by class, gate durations and flakes, escalations, owner time, doc-drift hits, onboarding time, **process overhead per slice** and **false-positive rate per check**. When overhead rose, it proposes at least one consolidation.
- **Guardrails:** tightening a gate, test, prompt or rubric line is agent-approved and logged (adding or tightening an **invariant** still needs the owner's approval, `INVARIANTS.md`); loosening (removing or weakening a gate, narrowing the verifier, weakening an invariant, changing the rubric) needs the owner; reverting a gate counts as loosening; preventive gates are judged by the seeded-defect catch rate. Check scripts have meta-tests (`scripts/tests/`). Flaky tests are quarantined at most N days with a backlog ID; INV-tagged and integrity-core tests are never quarantined.
- **Records:** routine process changes are `IMPROVEMENTS.md` entries plus the matching edit here, in the same change; invariant changes, rubric changes and the pilot result are ADRs.

## 10. Branches, releases, clearance

- `next` holds v0.4 and grows from an empty tree; `main` is the legacy line (no feature work); tag `legacy-final` = v0.3.0, never released. The read-only legacy worktree is the differential oracle (copies only).
- Commits go directly to `next` and agents push them (owner, 2026-10-24; ADR-0045); there are no pull requests, no agent GitHub account, no CODEOWNERS. **Temporary first-push gate:** until the owner has reviewed the setup of `next`, the pre-push hook refuses pushes unless `git config finzytrack.allowPush true` is set in the clone (only the owner sets it), and agents' `git push` asks first. Once the owner opens it, the decided flow applies.
- **Clearance by kind of edit** (owner, 2026-10-24): each mutator runs only on test copies until its kind is cleared: Add 50 writes / 1 week; Change/delete one entry 100 / 2 weeks; Many entries or several files 30 / 2 weeks with crash tests; twice those for a mutator with no old-app comparison (cleared on Beancount's own checks and hand-checked results). The owner signs one page per milestone. The final dogfood window is 4 weeks with one month-end reconciliation and one broker sync.
- **G1** (after the write core) uses measurable criteria; the owner judges the projected time to finish then (ADR on G1).
- **Release** = merge `next` into `main` when the release gate holds (ADR-0043: every mutator cleared, the real-ledger corpus, the dogfood window, the upgrade matrix from v0.2.2, packaged builds on three OSes, legal advice on the licence applied, architecture checks, the mutation floor, complete release notes). User docs ship from the docs `v0.4` branch.

## 11. Limits, stated honestly

Agents checking agents share blind spots; executable checks, Beancount as the oracle, spot audits, seeded defects and the owner's real use reduce this but do not remove it. The setup cost is front-loaded. Flaky end-to-end tests are the main threat to speed. The guard hooks are a safety net, not a sandbox.
