# Backlog: the single work list for the v0.4.0 rebuild

Purpose: one tracked list of every open work item for v0.4.0 (review findings, workstreams and invariant-enforcement items), each with a stable ID, a target milestone and a status.

This file is the tracked home of the private review backlog (`review:99`). The rebuild is a new app on branch `next`, built from an empty tree; v0.3.0 is frozen and is never patched. A finding is fixed by building its new module correctly from the first slice, with an acceptance or regression test written first.

## How this file works

- **IDs are stable.** Review finding IDs (`I1`, `L4-01`, `L8.M-1`, `N2`, …) are kept exactly as in the review. New items get `BL-nnnn`. IDs are never renumbered or reused.
- **Adding an item.** Any agent may add an item with the next free `BL-nnnn` ID, a one-line title, an area, a target milestone and a reference. Review findings are triaged per `PROCESS.md` before they are added. Every `TODO` in code cites an ID from this file; a `TODO` without an ID does not meet the definition of done.
- **Closing an item.** A slice closes an item. The commit message and the slice journal cite the ID. The status changes to `done` in the same commit.
- **Pending invariants.** An invariant whose check does not exist yet is marked "pending (BL-nnnn)" in `INVARIANTS.md`. CI accepts it only while that item is open, and fails at the exit of the item's milestone if the invariant is still pending. Closing the item without adding the check also fails CI. See "Invariant enforcement" below.
- **Milestone exit.** Every item is closed by a slice, not by time: an exit run (`FT_MILESTONE_EXIT=Mx python3 scripts/check_invariants.py`, roadmap §3.2) fails while any item of an `ID`-headed table here is `open` or `in-progress` and due by Mx (a bare `M1` counts as `M1a`). Keep item tables in that shape; the check refuses any other table with a milestone or status column (S0023).
- **Escalations** (questions for the owner) do not go here. They go to `design/inbox/<id>.md`, per `PROCESS.md`.
- **Decisions** do not go here either. Decisions live in ADRs (`design/decisions/`). The review's decision register is migrated there, not into this file.

**Status values**

| Status | Meaning |
|---|---|
| `open` | Not started. |
| `in-progress` | A slice is working on it. |
| `done` | Closed by a slice; the commit and journal cite the ID. |
| `removed` | Will not be built, by an owner product decision (the investment scope reset). Kept for the record; its milestone verifies the absence. |
| `post-v0.4` | Deferred to after v0.4.0. Not built now; the design must not foreclose it. |
| `parked` | A process check built only when an escape, a measurement or a periodic reflection shows the need (ADR-0058). The verifier covers it meanwhile. |

**Milestones** follow the milestone plan (`specs/roadmap.md`, summarising review:27) (M0 Foundations, M1 write-safety core (M1a + M1b), M2 shell/boot/open, M3 workspace lifecycle and legacy adoption, M4 Accounts, M5 Transactions and documents, M6 Import and reconciliation, M7 Dashboards/Analyze/Budgets, M8 Investments and prices, M9 Assistant, M10 Settings, M11 release hardening, M12 release). M10 to M12 close no new finding IDs; they re-verify.

**Citations.** Evidence for review findings stays in the private review archive and is cited as `review:<doc> §<section>`, for example `review:10 §1`, `review:L4`, `review:L8-verif §2`, `review:D1 §12.6`. File-and-line evidence, repro steps and long rationale are not copied here.

**Ground rules carried over from the review (owner, 2026-10-24).**
1. Everything ships together in v0.4.0. Tests and repros that work against a released version stay in the private archive until v0.4.0 ships.
2. Effort never filters a finding. Cost decides the order, not whether an item is built. Only an owner product decision removes a finding, and it is then listed as `removed`.
3. User docs move with the code: every change to behaviour, a format or a catalog updates the user docs on the docs release branch `v0.4` in the same slice.

---

## 1. Totals

| | Count |
|---|---|
| Finding index rows | 287 |
| Unique (canonical) findings | 240 |
| of which to build in v0.4.0 (`open`) | 229 |
| of which `done` | 3 |
| of which `post-v0.4` (hosted-only) | 4 |
| of which `removed` (investment scope reset) | 4 |
| Doubles (merged onto a canonical row) | 47 |
| Invariant-enforcement items (`BL-0001`…`BL-0031`) | 31 |
| Other new items (`BL-0032`…) | 13 |

Canonical findings to build, by milestone: M0 1, M1a 10, M1b 6, M2 47, M3 15, M4 18, M5 20, M6 40, M7 37, M8 23, M9 12, M10–M12 0 (total 229; S0023 closed L1-06 and L9-18 and split M1; S0025 closed L9.M-7).

---

## 2. Workstreams

Sixteen workstreams. Each one specifies a module or package of the new tree by its goal, its main work items and its acceptance. "Closes" lists canonical IDs; doubles follow in parentheses. Design references point at the private archive until the content is migrated into `design/` (ADRs, `ARCHITECTURE.md`, `design/specs/`).

**Retiring this section.** The roadmap, the capability map and the finding index now carry most of what these workstreams say. When an area's spec is written (in the first slice of its vertical), that workstream's goal and acceptance move into the spec and its section here is deleted in the same commit, leaving a one-line pointer (S0011).

### WS-QA: test infrastructure and enforcement
- **Goal.** Make every later change provable: characterization before risky changes, CI that runs the checks, corpora for the ledger and import seams, and the acceptance suite the frontend is built against.
- **Work items.**
  1. CI and pre-commit run backend pytest, vitest, the type check, ESLint (pins in `ARCHITECTURE.md`, `review:25 §10`), the licence check and the `THIRD_PARTY_NOTICES` check. `lint` does not change files; `lint:fix` does. CI runs deterministic gates only: no AI in CI (owner, 2026-10-24).
  2. D1 write corpus (public example ledgers, generated 5k/54k ledgers, hand-written hard cases) with property tests; D2 overlay-equivalence tests over the corpus.
  3. Beancount-generated domain vectors (tolerance, residuals, account and currency grammar, price map) for both sides. Written in S0024 (`design/specs/kernel/vectors.md`); the code passing them is BL-0002.
  4. Import fixture corpus (anonymized, owner-reviewed) with direct parser, converter, matcher, categorize and commit tests (`xfail(strict)` for known bugs).
  5. Acceptance suite (the L8 flows A1–A16 plus the verifier's flows), the `ft/v1` key-schema test, isolated backend on its own port and data dir, fresh demo per run, frozen clock on both sides, LLM stub, `TZ` west of UTC, `fixme` for flows whose capability has not arrived.
  6. Effect-based multi-user isolation matrix (with WS-ISOLATION) and the module-level-state check.
  7. Seed dashboards dry-run against the backend validator.
  8. API contract snapshot, per-endpoint (status, `code`) error matrix, SSE event protocols.
  9. Mirror surface spec with per-table checksums and a DDL snapshot. The spec is written in S0021 (`design/specs/mirror/`); the snapshot and checksums come from the M1a build (BL-0057).
  10. Per-mutator intent table with acknowledgeable error keys (needed before WS-WRITE stage 1). Written in S0004: `design/specs/write-path/` (50 rows; 2 wait for the owner; draft rows are confirmed in their vertical's first slice).
  11. Rule-reader, `.eml` and price-provider goldens.
  12. Recipe-run goldens (seed dashboards × parameter sets on the demo, frozen clock).
  13. Config and lifecycle current-state prose plus old-workspace fixtures → expected manifest and boot phase.
  14. Packaged-build CI matrix (Apple-silicon macOS ad-hoc signed, Windows 10/11 x64, Linux x64) with `--smoke-test` and an acceptance subset.
  15. Local real-ledger tier: copies of the owner's ledgers outside the repo; never in public CI (INV-23, INV-24).
  16. Differential-oracle harness: v0.3.0 from the legacy worktree as a separate process on ledger copies, compared through the D1 semantic diff with the expected divergence classes (I2, I3, I10, M28, FIX-<id>). Built for three mutators (update transaction, rename account, import append) in S0002 (spike S9); each further adapter is built in the first slice of its vertical (review:C DEP-11); BL-0045, BL-0046.
  17. All agent runs are local; the agent commits directly to `next`; owner approvals are quoted in the journal (owner, 2026-10-24).
  18. Legacy API tests as characterization reference: spike S12 measured 107 of 269 passing API-level tests running out of process through a thin shim (40%, below the 80% criterion), so they are reference reading for each vertical's characterization slice; `scripts/oracle/legacy_api_tests.py` runs them on demand and lists the 105 usable ones (S0003).
- **Acceptance.** A deliberately reintroduced I2, I5, L4-03 or L9-01 fails CI. CI is red on any test, type, lint or licence error. Every acceptance flow runs headless against an isolated backend with a frozen clock and an LLM stub.
- **Ref.** `review:99 §2 WS-QA`; `review:17 §2`; `review:D1 §6, §12.5`; `review:19 §3`.
- **Closes.** L9-18, L9.M-7. (2 canonical.)

### WS-SECURITY: security foundations
- **Goal.** The new shell is built with its security foundations from the first build, and they stay closed by construction (INV-09).
- **Work items.** One markdown renderer (markdown-it, raw HTML off, DOMPurify) for the assistant and every other markdown surface; strict CSP (`script-src 'self'`, no inline; a hash for the pre-paint theme script) on web and desktop (BL-0032); every interpolated value in chart formatters escaped (or rich-text mode); an allowlist of chart option keys, with HTML-bearing string formatters rejected; secrets referenced as `${VAR}`, redacted on every read and merged on write; attachments are untrusted tool inputs, never auto-applied; IMAP verifies certificates on every connect; Beancount's pickle cache is disabled; stored keys have explicit set/replace/clear with a `has_api_key` flag; a change of the AI endpoint URL needs confirmation.
- **Acceptance.** Every injection test case in the security corpus is inert; the CSP header is present on web and desktop; an IMAP server with a self-signed certificate is refused; no plaintext secret appears in any LLM request fixture; no pickle cache file is created or read.
- **Ref.** `review:13 §1, §4`; `review:14 §0`; `review:19 §1`; `review:18 §1`.
- **Closes.** L1-09, L6-01 (+ N3), L6-02, L6-03, N1, N2, L8-17, L8-20, L11.M-4. (9 canonical.)

### WS-BACKEND-CORE: the backend skeleton
- **Goal.** A layered backend with an explicit concurrency model, one error contract and one composition root, so each vertical slots into domain + use cases + thin routers.
- **Work items.** Workspace open model: nothing is swapped in place, and a failed open records nothing (INV-29); seeding and seed refresh under the lock; no ledger export endpoints. Thread-safety audit, then offload under a per-user `CapacityLimiter(1)`; a process-wide heavy-load semaphore; the error model (DomainError → APIError); `ConfigStore` with an injected root and logging callback; ledger version in the response envelope `meta`; root-relative paths for diagnostics and env files.
- **Acceptance.** `/health` answers in <100 ms during a 54k-ledger mutation; an un-awaited use case in an `async def` handler fails a check; every router error branch returns its pinned (status, code); the import-boundary test passes; a failed open records nothing.
- **Ref.** `review:L1 §1.2–1.7`; `review:10 §2–3`; `review:23 §3.9`.
- **Closes.** I6 (+ L10-03), L1-02 (+ L6-09), L1-04 (+ L10-11), L1-05, L1-06, L1-07, L1-12, L11.M-9. (8 canonical.)

### WS-WRITE: write-safety contract, splicing, overlay load
- **Goal.** No write can corrupt or lose ledger data: a write changes exactly what was intended, with one full load per write (INV-01, INV-22).
- **Stages.**
  1. Contract, built before any mutator: parser-level semantic diff, all-errors gate with explicit acknowledgement by location-insensitive error key, fingerprint re-stat under a file lock, atomic write with backup, read-back; every step fails closed. Plus the D2 overlay loader (pinned Beancount) with the `LoadPolicy` parameter, its equivalence suite and background `load_file` reconciliation; encrypted files refused.
  2. Splicing engine port (pure functions) with property tests.
  3. Inserts by splicing (import commit, opens, prices incl. the sidecar, budgets, documents, `balance` assertions).
  4. Replace/delete of single entries: transaction edit (weight check, price type, no partial costs, labels and `@@` kept, typed-metadata merge), targets addressed by `(TransactionId, entry_hash)`, undo journaled as entry text; balance CRUD; close/reopen. L3-03 is resolved first.
  5. Multi-entry: `move_postings` (rename, merge, delete with reassign/delete and an all-time preview), booking-method change preview.
  6. Every mutator promoted.
- **Promotion.** Per mutator. Until promoted, a mutator runs only on test copies and is compared with the old app. Clearance numbers per kind of edit (owner, 2026-10-24): add 50 writes / 1 week; change/delete 100 / 2 weeks; many entries or files 30 / 2 weeks with crash tests; mutators without an old-app comparison need twice the writes and time.
- **Acceptance.** Contract steps hold on every property-test step; apply-then-inverse returns the original bytes; overlay load equals `load_file` on the whole corpus; one full load per write; no resident ledger after a write; an injected failure in any contract step leaves the disk untouched; line shifts from a splice do not count old errors as new; an account delete that would break the ledger is refused or previewed.
- **Ref.** `review:D1 §3–12`; `review:10 §1a`; `review:18 §2`.
- **Closes.** I1 (+ L2-01), I2 (+ L1.M2), I3 (+ L1.M1), I9 (+ L1.M4), I10 (+ L1.M3), I11 (+ L1-08), L1-03, L3-02 (+ L3.M-5, L3.M-6, L8.M-3), L3-03 (+ L3.M-3, L3.M-4), L3-15. (10 canonical.)

### WS-MIRROR: the read side
- **Goal.** A mirror that is always consistent, exact where money is aggregated, safe for user SQL, and a documented, versioned surface (INV-05).
- **Work items.** Atomic generation publish (pointer-file fallback on Windows); `ledger_errors` as the D1 baseline; no `latest_prices` (v0.3.0 only; `price_asof` replaces it, CAP-QUERY-SURFACE); one QueryRunner (row cap, progress-handler timeout, one freshness check) for recipes, AI tools and Analyze; hardening (`query_only`, no `ATTACH`, statement authorizer, timeout, string-length limit); versioned `r_*` views; `dsum` (exact sum, returns REAL) and `price_asof` UDFs. Every mirror table name that existed at v0.2.2 keeps working through 0.4.x (owner, 2026-10-24); `lots` becomes a compatibility view over booked inventory with a deprecation notice (ADR-0048, re-confirmed by the owner); a recipe/SQL lint flags `lots`. Tables, columns and compatibility names: `design/specs/mirror/` (S0021).
- **Acceptance.** The swap test under concurrent reads has 0 errors, including an injected failed build; every seed query runs unchanged on QueryRunner; `ATTACH`, `VACUUM INTO`, `PRAGMA`, multi-statement input and a long-running query are refused; `dsum` matches an exact oracle and keeps `HAVING`/`ORDER BY` correct; outside tools can open the current generation read-only.
- **Ref.** `review:D1 §11`; `review:mirror-spike`; `review:14 §0a`; `review:11 Q9`.
- **Closes.** I4 (+ L1-01, L1.M5), L1-10, L3-07 (+ L3.M-1), L5-07 (+ L5.M3), L5-20. (5 canonical.)

### WS-KERNEL: shared domain kernel and Ledger Profile
- **Goal.** One pure module per domain primitive on each side; the backend owns ledger facts and serves them; the frontend never invents them (INV-02, INV-06, INV-07).
- **Work items.** Backend `app/domain`: money and quantize, tolerance via Beancount, account grammar from the options, currency grammar, PriceIndex (as-of, quote currency, staleness, same-day dedupe, future excluded with an "include scheduled" toggle), metadata keys, one `is_document_key`, `today(tz)`. Ledger Profile endpoint (roots incl. custom names, operating/reporting currencies, tolerance options, `dcontext` precision, currency roles, unknown account, metadata vocabulary incl. both key spellings, booking methods, ledger version). D10 account catalog and period balances. Frontend `src/domain`: money scale, `format.ts` on `Intl.NumberFormat`, `dates.ts` on `Temporal.PlainDate`, tolerance, parse, accounts, commodities, metadata; import bans per area; no hardcoded `USD` or `Expenses:Unknown`.
- **Acceptance.** Vector suites pass on both sides; `Assets:Retirement:401k`, `Café` and `日本` accepted and `_` rejected exactly as Beancount; `10.50` stays `10.50`; BTC shows 8 dp from `dcontext`; INR shows lakh/crore and USD millions under one separator setting; a user east of UTC after midnight gets the right today; no frontend file outside `src/domain` defines a formatter, today-helper, parser or root list.
- **Ref.** `review:11 §1–4`; `review:L3 §1`; `review:10 §2 D10`.
- **Closes.** L3-01, L3-04 (+ L3.M-2), L3-05 (+ L3.M-7, L3.M-8), L3-06, L3-08 (+ L9-19), L3-09, L3-10, L3-11, L3-12, L3-13, L3-14, L3-16, L3-17, L3.M-9, L3.M-10, L8-06, L8.M-12, L8.M-16. (18 canonical.)

### WS-IMPORT: import pipeline and statement reconciliation
- **Goal.** Every statement line is accounted for, re-import is idempotent by construction, and "why doesn't my balance match?" is answered by one matcher (INV-14).
- **Work items.** Built correct from the first Import slice: re-match at commit with skip-and-report and override; tags and links kept; only open slots are suggestion targets while dedupe covers all rows; correct CSV debit/credit signs; no `content_hash` matching (legacy hashes tolerated, never matched, never written); `import-key` written from the first commit as the only identity key. Then: adapters (tabular, OFX incl. multi-statement/CHARSET/CURDEF and a sign flip, email, LLM with declared date order, period check and sign anchor); normalize; server-side Draft sessions spilled to machine-local storage; posting-level matching (date window, 1:1 assignment, reference extraction, duplicate / transfer counterpart / settle pending); reconciliation mode (matched / missing / ledger-only / amount-differs, residual, closing assertion); categorization v2 (payee memory, direction, transfers, calibrated floor, batch predict); RuleStore with a v1→v2 reader before any format change; per-line currency; OFX "learn account" writes bank ids on the `open` directive through the write contract.
- **Acceptance.** Committing the same file twice writes nothing the second time and reports N skipped; a hand-written or externally entered transaction is matched; every input row lands in a bucket with counts shown; reconcile mode explains the full difference or shows the residual; existing rule files load unchanged; the old OFX mappings appear as `open` metadata after migration.
- **Ref.** `review:12 §1–5`; `review:L4-verif §3`; `review:28 §3–4`.
- **Closes.** I5, L4-01, L4-02, L4-03, L4-04, L4-05 (+ L4.M1), L4-06, L4-07, L4-08, L4-09, L4-10, L4-11, L4-12 (+ L6-13), L4-13, L4-14, L4-15, L4-16, L4.M2, L4.M3, L4.M4, L4.M5, L4.M6, L4.M7, L4.M8, L8-10, L8-11, L8-12, L8.M-10, L8.M-11. (29 canonical.)

### WS-RECIPES: recipe engine on the backend
- **Goal.** Dashboards stay user-authored JSON recipes, interpreted, validated and memoized once on the backend, on a correct financial vocabulary (INV-08).
- **Work items.** Backend interpreter with all transforms and typed per-currency money columns behind `POST /api/w/{ws}/recipes:run`; the `ledger` step with measures (balance, flow, value, cost, unrealized, realized from booking, income, budget, budget actual, reconciliation status); one strict validator returning JSON-path issues and rejecting unknown arguments; a viz registry as the extension contract; backend viz-input adapters in decimal; memo keyed on data version and transitive input hash; beanquery parameter binding; gzip; seed dashboards rewritten onto the `ledger` step (notice-plus-replace for edited copies). `budget_for_range` stays callable through 0.4.x as a deprecated alias (owner, 2026-10-24).
- **Acceptance.** All seed dashboards agree on Total Assets for any date, including a scheduled transaction and a future price; a no-cost holding counts in net worth; Budget History total equals the Overview total; income budgets show achieved vs target; a parameter change runs only affected steps; the AI can run what it writes and gets located errors.
- **Ref.** `review:14 §0–3`.
- **Closes.** I8 (+ L2.M-3.1, L5-16), L5-01, L5-02 (+ L5.M4), L5-03, L5-04, L5-05, L5-06, L5-08, L5-09, L5-10, L5-11 (+ L5.M6), L5-12, L5-13, L5-14, L5-15, L5-17, L5-18, L5-19, L5-22, L5.M1, L5.M2, L5.M5, L8-18, L8.M-13. (24 canonical.)

### WS-AI: one assistant over the use cases
- **Goal.** The AI is an adapter over the same use cases as the UI, with one write model (propose → preview → confirm → commit → undo) bound to server state (INV-13).
- **Work items.** LLM gateway (model capabilities, structured outputs, usage capture, caching, append-only server history, per-server model profiles); a bounded per-user `ConversationStore` with TTL that saves stopped turns; a typed event protocol generated to TS with call ids and a `user`-only input role; proposal infrastructure and approval card; figures from tools on the kernel; email extraction with direction, confidence and reject-on-missing-amount (as an import adapter); a server guardrail for "claimed a save, nothing proposed"; attachments parsed by import adapters and reconciled by the import matcher. Bring-your-own providers (OpenAI-compatible or Anthropic) and local models stay; Finzytrack AI mode is `post-v0.4` (owner, 2026-10-24).
- **Acceptance.** The proposal tamper cases (forged call, changed hash, bare call id, stale preview, other conversation, replay, edited arguments) are rejected; Stop cancels the server run and the turn is saved; a new write capability is one declaration over an existing use case; "reconcile this statement" works with a file attached.
- **Ref.** `review:13 §3–5`.
- **Closes.** L6-04, L6-05, L6-06, L6-07, L6-08, L6-10, L6-11, L6-12, L6-14, L6-15, L6-16 (+ N5), N4, L8-04, L8-05, L8.M-8, L8.M-9. (16 canonical.)

### WS-INVEST: investments, scoped
- **Goal.** Holdings, value, plain gains and an easy "matches my broker" workflow, with Beancount doing all booking.
- **Work items.** Not built (scope reset, owner): CAS "replace", XIRR/TWR/per-class returns, app-side lot computation, tax modelling, statement-import undo. Broker formats beyond CAS are `post-v0.4`. Built: seed dashboards on the `ledger` measures; per-account Holdings view with price date and age; prices subsystem (Yahoo, AMFI, I-bonds; unadjusted series; provenance; manual prices never overwritten; cadence and staleness); holdings reconciliation and one-upload sync (opening positions at the registrar's cost; events inferred, ambiguous ones listed, never guessed); every booking method Beancount books (FIFO/LIFO/HIFO with a dry-run lot preview, STRICT lot picker from as-of booked inventory, NONE and AVERAGE explained); CD life (principal from balance, `close` at maturity); `exposure` from the CAS category; dividend reinvestment quick action.
- **Acceptance.** A partial sale of a multi-lot holding works on every booking method; a backdated FIFO sale books the lot Beancount books; an upload with a missed reinvestment and a split produces one summary that, confirmed, makes units and value match and writes units assertions; no investment code computes lots.
- **Ref.** `review:16 §2–5`; `review:19 §5`.
- **Closes.** L7-01, L7-02 (+ L3.M-11), L7-03 (+ L7.M2, L7.M3), L7-04, L7-05, L7-06, L7-07, L7-08, L7-09, L7-10, L7-11, L7-12, L7-13, L7-14, L7-15, L7-16, L7-17, L7.M1, L7.M4, L7.M5, L7.M6, L8-26. (22 canonical.)

### WS-LIFECYCLE: one boot sequence, content packs, migrations
- **Goal.** First run is just a state; content delivery and every format migration are declarative, idempotent and backed up; no newer format is ever written (INV-17).
- **Work items.** Built into setup and boot from the start: a server-side existence check and mandatory backup before the wizard writes; a missing configured ledger is "ledger unavailable", never auto-created; no backup, no write; pristine means bytes equal any shipped version; the demo is replaced on update without asking, and an edited demo keeps one previous copy (owner, 2026-10-24). Then: the never-write-newer rule per workspace; non-destructive adoption of a v0.2/v0.3 install (old folders untouched, ledger files copied by default, Share as a gated option, a custom old-data location supported); boot state machine with UI states; plan/apply setup with a probe; content packs; the migration framework with dry-run and consent classes; the optional, consented legacy-metadata cleanup; the removed-compute-function migration with a located message; validated wizard currency; the launcher shows the boot state.
- **Acceptance.** Re-entering setup with an existing ledger adopts it; deleting the ledger file outside the app yields "ledger unavailable", not a new file; every migration is idempotent and leaves a backup or does nothing; a workspace from a newer app is refused with a restore option; adopting a v0.2.2 install leaves its folders byte-identical and v0.2.2 still starts on them; dev and packaged boot use the same code.
- **Ref.** `review:23 §3.2–3.9, §4.1–4.6`; `review:15 §2–4`; `review:D1 §13.3`.
- **Closes.** L8-15, L8-16, L10-02, L10-04, L10-05, L10-06, L10-07, L10-08, L10-09, L10-10, L10-13, L10-14, L10-15, L10-16, L10-18, L10.M-1, L10.M-2, L10.M-4. (18 canonical.)

### WS-ISOLATION: "don't foreclose, don't build" guards
- **Goal.** Keep hosting possible: every tenant-authored input is resolved through a policy object; no shared mutable state; desktop behaviour unchanged (INV-11, INV-28).
- **Work items.** Guard now: root injected into `ConfigStore`; absolute or escaping values refused for every path-like config field; BQL statement allowlist (`SELECT`/`BALANCES`/`JOURNAL`/`PRINT`); logging through an injected callback; destructive document operations never touch include files, the sidecar or `*.beancount`; storage keys prefixed by workspace. Seam now: a `LoadPolicy` argument on every load path (includes, documents roots, plugin allowlist, `insert_pythonpath`, encrypted files); an injected secret resolver. Conditions: the heavy-load semaphore is the only new process-wide state; every new cache is per user and bounded. Deferred (`post-v0.4`): quotas, multi-worker, proxy trust, provisioning, the hosted LoadPolicy body, a per-user secret store, outbound-URL controls, eviction refcounting.
- **Acceptance.** The isolation matrix (two tenants, effects checked after each case) is green with a test LoadPolicy; the globals check passes; desktop runs every flow unchanged with the permissive policy.
- **Ref.** `review:18 §1–5`.
- **Closes.** L1-11 (+ L11-04, L11-05), L10-01 (+ L11-01, L11.M-5), L10-12, L11-02, L11-03, L11-06, L11-07, L11-08 (+ L10.M-3), L11-09, L11-10, L11.M-1, L11.M-2, L11.M-3, L11.M-6, L11.M-7, L11.M-8. (16 canonical.)

### WS-FE-DATA: frontend data layer
- **Goal.** One server-state cache with domain-event invalidation, one API layer and one error path, as the foundation of the new frontend.
- **Work items.** Per-widget run token; generator resolution after commodities; `src/api` with one `http.call`, `AppError` and typed SSE readers; TanStack Query with workspace-scoped keys, `staleTime: Infinity` and monotonic version checks; the D10 account-catalog hook early; widget-as-query; notifications per D11 (toast vs modal, coalescing, history); bundle splitting.
- **Acceptance.** One request per widget per parameter change; no stale overwrite under a slow response; a ledger write or external edit invalidates exactly once; no component imports generated services directly; backend error messages reach the user once.
- **Ref.** `review:L2 §1`; `review:L2-verif §4`; `review:10 §2 D8–D11`; `review:23 §3.5`.
- **Closes.** I7 (+ L2-02), L2-03, L2-04 (+ L8-21), L2-05 (+ L2-13, L10-17), L2-06, L2-07, L2-10, L2-11, L2-12, L2-nits, L2.M-3.2, L2.M-3.3, L2.M-3.4. (13 canonical.)

### WS-UI: design system and primitives
- **Goal.** Views behave alike because they are built from the same components: `components/ui/` (vendored shadcn/ui on Radix; shadcn-vue on Reka if spike S13 keeps Vue) and `components/app/` domain primitives (INV-16, INV-18).
- **Work items.** Phase 0 foundation (tokens, pre-paint theme script, catalog hook); 1a confirm and unsaved guards; 1b pickers, then 1c dialogs; 2 navigation and feedback (toasts outside inert regions); 3 forms and display (`DateField` with a `Temporal.PlainDate` value, `MoneyText`); 4 tables (TanStack Table); Lucide icons. The new tree uses only these primitives from day one.
- **Acceptance.** Cancel is focused in every confirm; every dirty surface is guarded on route leave, tab switch, reload and dialog close; a toast raised inside a dialog is announced and clickable; token contrast ≥4.5:1 in both modes; the first frame is in the right theme; no raw `<button>`, `<input>`, `<table>`, `confirm()` or `toFixed` outside `ui/` and `app/`; a keyboard-only first run passes; no horizontal page scroll at ≤400 px.
- **Ref.** `review:17 §2–5`.
- **Closes.** L8.M-15, L9-01, L9-02, L9-03, L9-04, L9-05, L9-06, L9-07, L9-08, L9-09 (+ L9.M-4, L9.M-8), L9-10, L9-11, L9-12, L9-13 (+ L9.M-2), L9-14, L9-15, L9-16, L9-17, L9.M-1, L9.M-3, L9.M-5, L9.M-6. (22 canonical.)

### WS-FE-REWRITE: frontend verticals against the behaviour spec
- **Goal.** Views are thin compositions over headless feature hooks, built from the behaviour spec one vertical at a time (INV-12).
- **Work items.** Feature hooks such as `useEditBuffer` (per-operation inverse patches), `useTransactionSelection`, `useBulkEdit`, `useImportSession`, `useBudgetDrafts`; parents own data, the grid emits intents; one virtualized grid with one column model and the WAI-ARIA grid keyboard model. Order: shell on boot phases → Accounts → Transactions → Import → Dashboards → Investments → Assistant → Settings. Card views and HeadlessUI are never built. Each backend slice is built just before its vertical; every write is compared with the old app through the oracle.
- **Acceptance.** All acceptance flows green on the new UI, none `fixme`; the `ft/v1` key-schema test green; every behaviour-spec item implemented or listed as a DROP with its reason; no business rule exists only in a template; release notes list the accepted capability loss (phone in-place editing).
- **Ref.** `review:19 §2–4`; `review:L8 §1`; `review:L8-verif §2, §2.6`.
- **Closes.** L2-08, L2-09 (+ L8-22), L5-21, L8-01, L8-02, L8-03, L8-07, L8-08, L8-09, L8-13, L8-14, L8-19, L8-23 (+ L8.M-17), L8-24, L8-25, L8-27, L8-28, L8-29, L8-30, L8-31, L8-32, L8.M-1, L8.M-2, L8.M-4, L8.M-5, L8.M-6, L8.M-7, L8.M-14. (28 canonical.)

### WS-DOCS: user docs and developer rules
- **Goal.** Users and contributors read the truth on the day the version ships (INV-19).
- **Work items.** The docs release branch `v0.4` (created from the docs branch written for v0.3.0; never merged into the live docs branch before release); a user-doc update in every slice that changes behaviour; generated recipe-format and catalog reference; import and reconciliation guide; investments reference (scope reset, Holdings view, broker comparison, booking methods); AI data sent per capability; lifecycle (boot states, migrations, backups, refuse-newer and restore); multi-file ledgers; release notes listing every capability removed relative to v0.2.2, the keys Finzytrack no longer writes (`id`, `content_hash`, `source_account`), the downgrade comment-loss note and the `post-v0.4` capabilities; developer docs (`frontend/CLAUDE.md`, `THIRD_PARTY_NOTICES`, licence line, money-types amendment).
- **Acceptance.** Each change links its docs commit on `v0.4`; generated catalog docs are rebuilt in CI; the docs build is clean.
- **Closes.** No findings directly; it is the definition-of-done gate for all others.

---

## 3. Finding index

Every review finding, post-verification. Severity is the verified desktop value unless marked hosted. Security findings show `security` instead of a level until v0.4.0 ships (the levels are in the private review archive). Titles state the target behaviour. "Area" is the owning workstream; "Milestone" is where the finding is closed (`review:27 App. A.2`). Refuted findings are not listed.

### 3.1 I-series (integrity) (11 canonical)

Evidence: `review:10 §1` (I-series); index `review:99 §4.1`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| I1 | Account delete shows an all-time preview of every transaction it removes, with a real choice | High | WS-WRITE | M4 | open | L2-01 | `review:10 §1` |
| I2 | Glob includes are never expanded into the root file on write | High | WS-WRITE | M1b | open | L1.M2 | `review:10 §1` |
| I3 | Plugin-generated entries are never written to disk | High | WS-WRITE | M1b | open | L1.M1 | `review:10 §1` |
| I4 | The mirror is published atomically; readers never see an empty or partial mirror | High | WS-MIRROR | M1a | open | L1-01, L1.M5 | `review:10 §1` |
| I5 | Duplicate detection does not use the legacy content hash (tolerated, never matched, never written) | Medium | WS-IMPORT | M6 | open | — | `review:10 §1` |
| I6 | A failed workspace open records nothing; nothing is swapped in place | High | WS-BACKEND-CORE | M2 | open | L10-03 | `review:10 §1` |
| I7 | Dashboard widgets never show results for stale parameters | High | WS-FE-DATA | M7 | open | L2-02 | `review:10 §1` |
| I8 | Generators resolve after commodities load; widgets run once, in the right currency | High | WS-RECIPES | M7 | open | L2.M-3.1, L5-16 (also L3-10) | `review:10 §1` |
| I9 | A write is refused when a ledger file changed on disk after it was read | Medium | WS-WRITE | M1b | open | L1.M4 | `review:10 §1` |
| I10 | Writes splice source text: elided amounts, `{}` reductions and comments are kept as written | Medium | WS-WRITE | M1b | open | L1.M3 | `review:10 §1` |
| I11 | Account delete leaves no dangling directives; the write gate checks all new errors | High | WS-WRITE | M4 | open | L1-08 | `review:10 §1` |

### 3.2 L1 backend architecture (10 canonical)

Evidence: `review:L1`, `review:L1-verif`; index `review:99 §4.2`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L1-02 | Blocking parse, write and export work runs off the event loop under a per-user limiter | High | WS-BACKEND-CORE | M1a | open | L6-09 | `review:L1` |
| L1-03 | One full ledger load per write | High | WS-WRITE | M1b | open | — | `review:L1` |
| L1-04 | Seed refresh and seeding run under the write lock; no manual export endpoints | High | WS-BACKEND-CORE | M2 | open | L10-11 | `review:L1` |
| L1-05 | One backend error model: DomainError mapped to APIError at the edge | Medium | WS-BACKEND-CORE | M1a | open | — | `review:L1` |
| L1-06 | Layering enforced from the first commit (core imports nothing from api, services or the app entry) | Medium | WS-BACKEND-CORE | M0 | done | — | `review:L1`; closed by S0006 (`backend/.importlinter` layers; pre-push, ci.yml), S0023 |
| L1-07 | One composition root per user (UserContainer) | Low | WS-BACKEND-CORE | M2 | open | — | `review:L1` |
| L1-09 | Beancount's pickle cache is disabled; no cache file is created or read | security | WS-SECURITY | M1a | open | — | `review:L1` |
| L1-10 | One QueryRunner with a freshness check, row cap and working timeout | Medium | WS-MIRROR | M1a | open | — | `review:L1` |
| L1-11 | AI saves are bound to their proposal; no process-global preview or token state | Low-Med (desktop) / High (hosted) | WS-ISOLATION | M9 | open | L11-04, L11-05 | `review:L1` |
| L1-12 | Dead code and misnamed modules are not carried over | Low | WS-BACKEND-CORE | M2 | open | — | `review:L1` |

### 3.3 L2 frontend data layer (14 canonical)

Evidence: `review:L2`, `review:L2-verif`, `review:10`; index `review:99 §4.3`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L2-03 | Server-state cache with query keys and domain-event invalidation | High | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2-04 | Each widget runs once on mount and once per parameter change | Medium | WS-FE-DATA | M7 | open | L8-21 | `review:L2` |
| L2-05 | One frontend error path: backend messages reach the user once | High | WS-FE-DATA | M2 | open | L2-13, L10-17 | `review:L2` |
| L2-06 | One API access layer (`src/api`) for every call and SSE reader | Medium | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2-07 | Shared hooks have no per-call side effects (no listener or timer leaks) | Low | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2-08 | The transaction edit buffer has one owner; no deep watchers or deep copies | Medium | WS-FE-REWRITE | M5 | open | — | `review:L2` |
| L2-09 | Charts resize with their container | Medium | WS-FE-REWRITE | M7 | open | L8-22 (also L5-21) | `review:L2` |
| L2-10 | Route-level code splitting; no dead dependencies | Low | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2-11 | Directory roles are real: pure modules apart from hooks and data fetching | Medium | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2-12 | App-built SQL uses bound parameters; queries are not logged by default | Low | WS-FE-DATA | M5 | open | — | `review:L2` |
| L2-nits | Config is fetched once at startup; no shadowed globals | Low | WS-FE-DATA | M2 | open | — | `review:L2` |
| L2.M-3.2 | Recipe loading has a correct in-flight guard | Low | WS-FE-DATA | M2 | open | — | `review:L2-verif` |
| L2.M-3.3 | A dashboard tab switch does not re-run the whole board | Medium | WS-FE-DATA | M7 | open | — | `review:L2-verif` |
| L2.M-3.4 | Assistant ledger writes invalidate caches through the ledger version | Medium | WS-FE-DATA | M2 | open | — | `review:L2-verif` |

### 3.4 L3 domain kernel (19 canonical)

Evidence: `review:L3`, `review:L3-verif`, `review:11`; index `review:99 §4.4`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L3-01 | The sale form submits exact units (no 2-dp rounding) | Critical | WS-KERNEL | M8 | open | — | `review:L3` |
| L3-02 | Transactions with a cost or price can be edited (`@@`, cost date and lot label kept) | High | WS-WRITE | M5 | open | L3.M-5, L3.M-6, L8.M-3 | `review:L3` |
| L3-03 | `@@` total prices are kept as written (design point open, D1 §8; resolve before WS-WRITE stage 4) | Medium | WS-WRITE | M5 | open | L3.M-3, L3.M-4 | `review:L3` |
| L3-04 | Account-name validation matches Beancount, including custom root names | High | WS-KERNEL | M4 | open | L3.M-2 | `review:L3` |
| L3-05 | The written decimal scale is preserved; derived amounts are quantized | High | WS-KERNEL | M4 | open | L3.M-7, L3.M-8 | `review:L3` |
| L3-06 | Period balances are exact; Equity is cumulative; one meaning of "as of" | High | WS-KERNEL | M4 | open | — | `review:L3` |
| L3-07 | SQL money aggregation uses an exact sum (`dsum`) that returns REAL | Medium | WS-MIRROR | M7 | open | L3.M-1 | `review:L3` |
| L3-08 | Per-commodity display precision from `dcontext`; one formatter | High | WS-KERNEL | M4 | open | L9-19 | `review:L3` |
| L3-09 | One balance check per side, matching Beancount's tolerance | Medium | WS-KERNEL | M5 | open | — | `review:L3` |
| L3-10 | "Today" is the user's civil date; email header dates are normalized | Medium | WS-KERNEL | M2 | open | — | `review:L3` |
| L3-11 | Amount and date parsers handle every sign form and reject impossible dates | High | WS-KERNEL | M6 | open | — | `review:L3` |
| L3-12 | One PriceIndex: as-of, quote currency, staleness, future prices excluded | Medium | WS-KERNEL | M7 | open | — | `review:L3` |
| L3-13 | Reporting currency and cash are separate concepts (currency roles) | Medium | WS-KERNEL | M8 | open | — | `review:L3` |
| L3-14 | The unknown-account default comes from the Ledger Profile | Low | WS-KERNEL | M4 | open | — | `review:L3` |
| L3-15 | Typed metadata keeps its type on edit; one document-key rule | Medium | WS-WRITE | M5 | open | — | `review:L3` |
| L3-16 | Dead constants removed; one meaning of `source_account` | Low | WS-KERNEL | M4 | open | — | `review:L3` |
| L3-17 | Small kernel fixes (commodity regex, treemap rollup, sort comparators, OFX currency default) | Low | WS-KERNEL | M7 | open | — | `review:L3` |
| L3.M-9 | Rounding is HALF_EVEN on both sides | Low | WS-KERNEL | M4 | open | — | `review:L3-verif` |
| L3.M-10 | Exponent-form decimals are formatted for display | Low | WS-KERNEL | M4 | open | — | `review:L3-verif` |

### 3.5 L4 import pipeline (23 canonical)

Evidence: `review:L4`, `review:L4-verif`, `review:12`; index `review:99 §4.5`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L4-01 | Autocategorize fills only open posting slots; other legs are untouched | Critical | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-02 | Import commit re-matches, skips and reports duplicates, with an explicit override | Critical | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-03 | CSV split debit/credit columns parse correctly | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-04 | Every input row lands in a bucket; an import reconciles to the statement balance | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-05 | A posting-level matcher sees every ledger entry, not only imported ones | Critical | WS-IMPORT | M6 | open | L4.M1 | `review:L4` |
| L4-06 | Tags and links are kept on commit | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-07 | Each staged import keeps its own source account | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-08 | Categorization uses amount and direction and can propose transfers | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-09 | LLM file parsing uses a declared date order, a period check and a sign anchor | Medium | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-10 | The source account is open over the line dates; a new account opens at the earliest line | High | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-11 | Foreign-currency and pending lines are represented; no silent USD default | Medium | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-12 | One RuleStore with atomic writes and a strict, versioned rule format | Medium | WS-IMPORT | M6 | open | L6-13 | `review:L4` |
| L4-13 | Batch categorization prediction, off the event loop | Medium | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-14 | One categorization service (no duplicated engine resolution) | Low-Med | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-15 | One canonical Draft contract, generated to TypeScript | Medium | WS-IMPORT | M6 | open | — | `review:L4` |
| L4-16 | Small import fixes (balance check, OFX multi-statement/CURDEF/CHARSET, XLS text, zero cost) | Low | WS-IMPORT | M6 | open | — | `review:L4` |
| L4.M2 | The classifier handles payee-less entries | Medium | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M3 | No silent currency defaults on import paths (profile reporting currency) | Low-Med | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M4 | OFX amount sign has a per-mapping flip option | Medium | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M5 | XLS reader semantics are characterized before parsing moves to the backend | Medium | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M6 | Constraints between import fixes are respected (L4-01 with L3-14; L4-02 skip-and-report; I5 with 1:1 matching) | Process | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M7 | Proposed balance assertions handle pending entries and intraday `DTASOF` | High | WS-IMPORT | M6 | open | — | `review:L4-verif` |
| L4.M8 | The idempotency key does not hash the account; anchors are immutable during review | Medium | WS-IMPORT | M6 | open | — | `review:L4-verif` |

### 3.6 L5 recipe engine (24 canonical)

Evidence: `review:L5`, `review:L5-verif`, `review:14`; index `review:99 §4.6`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L5-01 | One `ledger`-step valuation: all dashboards agree on Total Assets for a date | High | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-02 | Recipes are interpreted on the backend | High (arch) | WS-RECIPES | M7 | open | L5.M4 | `review:L5` |
| L5-03 | One strict backend recipe validator | Medium | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-04 | Budget totals count nested budgets once | High | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-05 | Income budgets compute achieved vs target correctly | Med-High | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-06 | A parameter change re-runs only the affected steps (memo) | Med-High | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-07 | User SQL runs read-only; `ATTACH` and file-creating statements are refused | Low (desktop) / Low-Med (hosted) | WS-MIRROR | M1a | open | L5.M3 | `review:L5` |
| L5-08 | Unknown compute arguments are rejected | Medium | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-09 | BQL parameters use beanquery binding | Low-Med | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-10 | The recipe schema is strict (warn first) | Medium | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-11 | Realized gains come from booking, including unannotated sales and fees | Medium | WS-RECIPES | M8 | open | L5.M6 | `review:L5` |
| L5-12 | Budget views convert to the reporting currency | Medium | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-13 | Tabular steps keep currencies apart; pivot keeps duplicate cells | Low-Med | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-14 | One viz registry is the single source of chart types | Medium | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-15 | The dead TypeScript-recipe path is not carried over | Low-Med | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-17 | Widget settings are keyed per dashboard | Low | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-18 | Gallery labels reviewed for finance accuracy | Low | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-19 | Account roots in recipes come from the Ledger Profile | Low | WS-RECIPES | M7 | open | — | `review:L5` |
| L5-20 | Recipe SQL targets versioned `r_*` views | Low | WS-MIRROR | M7 | open | — | `review:L5` |
| L5-21 | Charts update without a full option reset | Low | WS-FE-REWRITE | M7 | open | — | `review:L5` |
| L5-22 | Reconciliation status per account and currency, with "reconciled through" | Low | WS-RECIPES | M7 | open | — | `review:L5` |
| L5.M1 | Same-day prices do not duplicate holdings rows | Medium | WS-RECIPES | M7 | open | — | `review:L5-verif` |
| L5.M2 | Holdings without a cost basis count in net worth | High | WS-RECIPES | M7 | open | — | `review:L5-verif` |
| L5.M5 | The current-budget rule comes from the backend, not a frontend copy | Low | WS-RECIPES | M4 | open | — | `review:L5-verif` |

### 3.7 L6 AI subsystem (17 canonical)

Evidence: `review:L6`, `review:L6-verif`, `review:13`; index `review:99 §4.7`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L6-01 | Assistant markdown is rendered with HTML disabled and sanitized, under a strict CSP (INV-09) | security | WS-SECURITY | M2 | open | N3 | `review:L6` |
| L6-02 | Secrets in rule files are `${VAR}` references and are redacted before any model request | security | WS-SECURITY | M3 | open | — | `review:L6` |
| L6-03 | Attachments are untrusted input; AI writes are proposals that need confirmation | security | WS-SECURITY | M9 | open | — | `review:L6` |
| L6-04 | AI capabilities are adapters over the UI's use cases | High | WS-AI | M9 | open | — | `review:L6` |
| L6-05 | One intent-driven assistant; an attached file keeps the ledger tools available | High | WS-AI | M9 | open | — | `review:L6` |
| L6-06 | Analyst figures come from tools on the kernel (exact, holdings included) | High | WS-AI | M9 | open | — | `review:L6` |
| L6-07 | AI email extraction records direction and confidence; a missing amount is rejected | High | WS-AI | M6 | open | — | `review:L6` |
| L6-08 | Provider layer kept current (Pydantic AI, model capabilities, server history) | High | WS-AI | M6 | open | — | `review:L6` |
| L6-10 | Email rules are tested against the same body and extractor used at fetch | Medium | WS-AI | M6 | open | — | `review:L6` |
| L6-11 | LLM JSON comes from structured outputs | Medium | WS-AI | M9 | open | — | `review:L6` |
| L6-12 | NL transaction checks are per currency; the default currency comes from the profile | Medium | WS-AI | M6 | open | — | `review:L6` |
| L6-14 | One LLM gateway with history caching and usage capture | Medium | WS-AI | M6 | open | — | `review:L6` |
| L6-15 | Server-side conversation store; typed, generated event protocol | Medium | WS-AI | M9 | open | — | `review:L6` |
| L6-16 | Prompt loading and AI error handling in one module | Low | WS-AI | M6 | open | N5 | `review:L6` |
| N1 | Dashboard options are data: HTML-bearing formatters are rejected by the validator | security | WS-SECURITY | M7 | open | — | `review:L6-verif §4` |
| N2 | One sanitized markdown renderer replaces the hand-written one | security | WS-SECURITY | M2 | open | — | `review:L6-verif §4` |
| N4 | Client context is validated by the typed protocol | Low | WS-AI | M9 | open | — | `review:L6-verif §4` |

### 3.8 L7 investments (21 canonical)

Evidence: `review:L7`, `review:L7-verif`, `review:16`; index `review:99 §4.8`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L7-01 | Windowed XIRR (cut by the investment scope reset) | High | WS-INVEST | none (M8 verifies absence) | removed | — | `review:L7` |
| L7-02 | Per-asset-class XIRR (cut by the investment scope reset) | Medium (High once exposure ships) | WS-INVEST | none (M8 verifies absence) | removed | L3.M-11 | `review:L7` |
| L7-03 | No CAS "replace": an investment import only adds | High | WS-INVEST | M8 | open | L7.M2, L7.M3 | `review:L7` |
| L7-04 | Statement import never writes failing assertions or exempts errors from the gate | High | WS-INVEST | M8 | open | — | `review:L7` |
| L7-05 | Investment statement preview reads the mirror; one write per import | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-06 | Charges and taxes as the broker shows them; one charges account | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-07 | Lot selection uses as-of booked inventory and lot labels | Med-High | WS-INVEST | M8 | open | — | `review:L7` |
| L7-08 | `cash_instruments` honours its as-of contract | Low-Med | WS-INVEST | M8 | open | — | `review:L7` |
| L7-09 | The price sidecar is a write-contract member file, with an opt-in include | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-10 | Exposure comes from the CAS category; schemes are not all labelled `mutual-fund` | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-11 | Investment cash matching uses the shared import matcher | Low-Med | WS-INVEST | M8 | open | — | `review:L7` |
| L7-12 | CD maturity uses the account balance and closes the account | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-13 | Price history is unadjusted (or consistently adjusted); manual prices are never overwritten | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-14 | Investment values convert currency through the PriceIndex | Medium | WS-INVEST | M8 | open | — | `review:L7` |
| L7-15 | No in-memory statement token store | Low | WS-INVEST | M8 | open | — | `review:L7` |
| L7-16 | Investments and prices are separate, layered modules | Low-Med | WS-INVEST | M8 | open | — | `review:L7` |
| L7-17 | XIRR solver details (cut by the investment scope reset) | Low | WS-INVEST | none (M8 verifies absence) | removed | — | `review:L7` |
| L7.M1 | Opening lots at cost in XIRR (cut by the investment scope reset) | High | WS-INVEST | none (M8 verifies absence) | removed | — | `review:L7-verif` |
| L7.M4 | Gain labels follow the scope-reset definitions | Low-Med | WS-INVEST | M8 | open | — | `review:L7-verif` |
| L7.M5 | Instrument type, exposure and tax class are separate concepts | Design | WS-INVEST | M8 | open | — | `review:L7-verif` |
| L7.M6 | The undo confirm states its effect after later edits | Low | WS-INVEST | M8 | open | — | `review:L7-verif` |

### 3.9 L8 frontend components (45 canonical)

Evidence: `review:L8`, `review:L8-verif`, `review:19`; index `review:99 §4.9`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L8-01 | Re-running a Transactions query keeps unsaved edits | High | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8-02 | Edits typed during Save are sent, not marked saved | High | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8-03 | Grid keyboard and DOM handlers are scoped to the active instance | Low-Med | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8-04 | Assistant answers stream visibly | Medium | WS-AI | M9 | open | — | `review:L8` |
| L8-05 | Assistant work can be cancelled; unsent drafts are kept | Medium | WS-AI | M9 | open | — | `review:L8` |
| L8-06 | No hardcoded `USD` or `Expenses:Unknown` defaults in the frontend | Medium | WS-KERNEL | M5 | open | — | `review:L8` |
| L8-07 | Numeric cells accept partial input | Low | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8-08 | Resize state is saved on pointer-up; stored maps are pruned | Low | WS-FE-REWRITE | M2 | open | — | `review:L8` |
| L8-09 | The Prices tab renders after any tab visit | Medium | WS-FE-REWRITE | M8 | open | — | `review:L8` |
| L8-10 | The duplicate dialog handles the last item; "Keep" is recorded | Medium | WS-IMPORT | M6 | open | — | `review:L8` |
| L8-11 | Import staging de-duplicates sources; a reset affects one source only | Medium | WS-IMPORT | M6 | open | — | `review:L8` |
| L8-12 | Autocategorize is safe when rows are added mid-request | Low-Med | WS-IMPORT | M6 | open | — | `review:L8` |
| L8-13 | Rule and dashboard editors save to the file they loaded | Medium | WS-FE-REWRITE | M6 | open | — | `review:L8` |
| L8-14 | A workspace switch keeps unsaved Settings edits (no remount) | Medium | WS-FE-REWRITE | M2 | open | — | `review:L8` |
| L8-15 | A wrong ledger path never creates a new ledger | Medium | WS-LIFECYCLE | M3 | open | — | `review:L8` |
| L8-16 | The setup-wizard currency is validated against the commodity grammar | Medium | WS-LIFECYCLE | M3 | open | — | `review:L8` |
| L8-17 | Stored API keys and tokens can be set, replaced and cleared | security | WS-SECURITY | M6 | open | — | `review:L8` |
| L8-18 | Seed column formats are applied; dates are parsed as civil dates | Medium | WS-RECIPES | M7 | open | — | `review:L8` |
| L8-19 | The calendar heatmap range follows parameter changes | Medium | WS-FE-REWRITE | M7 | open | — | `review:L8` |
| L8-20 | Chart tooltips escape interpolated ledger values | security | WS-SECURITY | M7 | open | — | `review:L8` |
| L8-23 | Budget drafts survive refreshes; a re-save never duplicates directives | Med-High | WS-FE-REWRITE | M7 | open | L8.M-17 | `review:L8` |
| L8-24 | Balance assertions use exact amounts and the as-of balance | Medium | WS-FE-REWRITE | M4 | open | — | `review:L8` |
| L8-25 | Busy states block double submit; edit mode starts clean | Low-Med | WS-FE-REWRITE | M4 | open | — | `review:L8` |
| L8-26 | Quick-action suggestions are per field and never carry over | Medium | WS-INVEST | M8 | open | — | `review:L8` |
| L8-27 | The Accounts view handles load failure, races and "All Time" correctly | Low-Med | WS-FE-REWRITE | M4 | open | — | `review:L8` |
| L8-28 | A dashboard tab switch does not inherit URL params; same-dashboard links work | Medium | WS-FE-REWRITE | M7 | open | — | `review:L8` |
| L8-29 | Shell Escape handling respects modals; one resize listener | Low | WS-FE-REWRITE | M2 | open | — | `review:L8` |
| L8-30 | Every error reaches the user through one error path | Low-Med | WS-FE-REWRITE | M2 | open | — | `review:L8` |
| L8-31 | Minor state quirks are folded into the behaviour spec | Low | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8-32 | A controlled transaction grid; the view owns the edit buffer | Medium (arch) | WS-FE-REWRITE | M5 | open | — | `review:L8` |
| L8.M-1 | Undo uses per-operation inverse patches (undone changes never return) | High | WS-FE-REWRITE | M5 | open | — | `review:L8-verif` |
| L8.M-2 | A cleared memo is saved | Medium | WS-FE-REWRITE | M5 | open | — | `review:L8-verif` |
| L8.M-4 | Bulk operations act only on visible selected rows | Medium | WS-FE-REWRITE | M5 | open | — | `review:L8-verif` |
| L8.M-5 | Metadata rename respects existing keys, reserved keys and tag grammar | Low-Med | WS-FE-REWRITE | M5 | open | — | `review:L8-verif` |
| L8.M-6 | Account drill-through uses exact or subtree match; the statement is not capped | Low-Med | WS-FE-REWRITE | M4 | open | — | `review:L8-verif` |
| L8.M-7 | Balance breakdown sorts correctly | Low | WS-FE-REWRITE | M4 | open | — | `review:L8-verif` |
| L8.M-8 | Assistant validation is keyed to the proposal id | Medium | WS-AI | M9 | open | — | `review:L8-verif` |
| L8.M-9 | A recipe preview does not change saved dashboard selections | Medium | WS-AI | M9 | open | — | `review:L8-verif` |
| L8.M-10 | Import AI, email and NL results survive tab switches; requests can be aborted | Medium | WS-IMPORT | M6 | open | — | `review:L8-verif` |
| L8.M-11 | Blank import amounts are auto-balanced or rejected, never written as 0 | Medium | WS-IMPORT | M6 | open | — | `review:L8-verif` |
| L8.M-12 | Import summary dates are shown as civil dates | Low | WS-KERNEL | M6 | open | — | `review:L8-verif` |
| L8.M-13 | Dashboard reload and link capabilities work | Low-Med | WS-RECIPES | M7 | open | — | `review:L8-verif` |
| L8.M-14 | A load failure shows an error state, not a false empty state | Low | WS-FE-REWRITE | M8 | open | — | `review:L8-verif` |
| L8.M-15 | Repeated toasts show again; toasts use a live region | Low-Med | WS-UI | M2 | open | — | `review:L8-verif` |
| L8.M-16 | Date presets, stale guards and integer formatting are correct | Low | WS-KERNEL | M5 | open | — | `review:L8-verif` |

### 3.10 L9 UI consistency (23 canonical)

Evidence: `review:L9`, `review:L9-verif`, `review:17`; index `review:99 §4.10`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L9-01 | Confirm dialogs focus Cancel | High | WS-UI | M2 | open | — | `review:L9` |
| L9-02 | One confirmation pattern (`useConfirm`); no native `confirm()` | Low-Med | WS-UI | M2 | open | — | `review:L9` |
| L9-03 | Dialog forms keep drafts on Escape or outside click | Med-High | WS-UI | M2 | open | — | `review:L9` |
| L9-04 | One dialog shell; the startup gate has a role and a focus trap | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-05 | Every icon-only button has an accessible name | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-06 | Labels are linked to inputs; errors are announced | Med-High | WS-UI | M2 | open | — | `review:L9` |
| L9-07 | One accessible tab component; the focus ring is kept | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-08 | One select/combobox family; menus flip and are not clipped | Medium | WS-UI | M4 | open | — | `review:L9` |
| L9-09 | Account pickers select existing accounts only ("New account…" opens a blank form) | Medium | WS-UI | M4 | open | L9.M-4 (also L4-10), L9.M-8 | `review:L9` |
| L9-10 | Every interaction works with the keyboard | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-11 | Theme tokens and a pre-paint script; the first frame is in the right theme | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-12 | Helper text meets WCAG contrast | Medium | WS-UI | M2 | open | — | `review:L9` |
| L9-13 | Toasts are announced and usable over dialogs | Medium | WS-UI | M2 | open | L9.M-2 | `review:L9` |
| L9-14 | One loading/empty/error state family | Low-Med | WS-UI | M2 | open | — | `review:L9` |
| L9-15 | One DataTable for interactive tables | Medium | WS-UI | M5 | open | — | `review:L9` |
| L9-16 | Button and input primitives; no iOS zoom on inputs | Low | WS-UI | M2 | open | — | `review:L9` |
| L9-17 | One icon set (Lucide) | Low | WS-UI | M2 | open | — | `review:L9` |
| L9-18 | CI runs lint, type check and frontend tests; lint does not change files | Low-Med (enabler) | WS-QA | M0 | done | — | `review:L9`; closed by S0009 (eslint without fix, vue-tsc, vitest in pre-commit, pre-push, ci.yml), S0023 |
| L9.M-1 | A Settings tab switch guards unsaved edits | High | WS-UI | M2 | open | — | `review:L9-verif` |
| L9.M-3 | One unsaved-change guard covering route, reload, tab and dialog | Medium | WS-UI | M2 | open | — | `review:L9-verif` |
| L9.M-5 | Pickers migrate before their dialogs (ordering constraint) | Plan (High if missed) | WS-UI | M2 | open | — | `review:L9-verif` |
| L9.M-6 | Help text and tooltips are portalled, not clipped | Low | WS-UI | M2 | open | — | `review:L9-verif` |
| L9.M-7 | Tests stub components by import, not by name | Low | WS-QA | M0 | done | — | `review:L9-verif`; closed by S0025 (lint ban in test files) |

### 3.11 L10 app lifecycle (18 canonical)

Evidence: `review:L10`, `review:L10-verif`, `review:15`; index `review:99 §4.11`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L10-01 | Path-like config fields are confined to the workspace root | Critical (hosted) / none (desktop) | WS-ISOLATION | M2 | open | L11-01, L11.M-5 | `review:L10` |
| L10-02 | Setup never overwrites an existing ledger; a backup is mandatory | High | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-04 | One boot sequence; services are built after setup decisions | Medium | WS-LIFECYCLE | M2 | open | — | `review:L10` |
| L10-05 | Content packs replace the separate seeding entry points | Medium | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-06 | A seed file is pristine when its bytes equal any shipped version | Medium | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-07 | Migrations are idempotent and never delete partially | Medium | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-08 | Never write a newer format; a newer workspace is refused, with restore | Medium (latent) | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-09 | Server-side readiness gate (`GET /api/boot`) | Low | WS-LIFECYCLE | M2 | open | — | `review:L10` |
| L10-10 | One migration framework (no coupled file-mutating engines) | Low | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-12 | Hosted registry locking and provisioning | Low (hosted) | WS-ISOLATION | post-v0.4 | post-v0.4 | — | `review:L10` |
| L10-13 | A missing ledger directory shows a boot state | Medium | WS-LIFECYCLE | M2 | open | — | `review:L10` |
| L10-14 | The demo notice says when the demo is the active ledger | Low | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-15 | Versioned config; dead flags removed; one port default | Low | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-16 | Per-run lifecycle backups, bounded | Low | WS-LIFECYCLE | M3 | open | — | `review:L10` |
| L10-18 | Builds of one tag are reproducible | Low | WS-LIFECYCLE | M0 | open | — | `review:L10` |
| L10.M-1 | A missing ledger is "ledger unavailable", never created | Med-High | WS-LIFECYCLE | M2 | open | — | `review:L10-verif` |
| L10.M-2 | No backup, no write | Medium | WS-LIFECYCLE | M3 | open | — | `review:L10-verif` |
| L10.M-4 | The setup probe rejects unparseable or non-UTF-8 files | Low | WS-LIFECYCLE | M3 | open | — | `review:L10-verif` |

### 3.12 L11 multi-user (15 canonical)

Evidence: `review:L11`, `review:L11-verif`, `review:18`; index `review:99 §4.12`.

| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L11-02 | Ledger includes are confined by LoadPolicy | Critical (hosted) | WS-ISOLATION | M1a | open | — | `review:L11` |
| L11-03 | Logging is configured through an injected callback, not by a settings change | Medium (hosted) | WS-ISOLATION | M1a | open | — | `review:L11` |
| L11-06 | Documents roots are confined by LoadPolicy | Critical (hosted) | WS-ISOLATION | M1a | open | — | `review:L11` |
| L11-07 | BQL is limited to a statement allowlist | Low-Med | WS-ISOLATION | M7 | open | — | `review:L11` |
| L11-08 | Isolation tests check effects, not only the changed field | Test gap | WS-ISOLATION | M2 | open | L10.M-3 | `review:L11` |
| L11-09 | Hosted eviction, cwd-relative files and user-id case | Low (hosted) | WS-ISOLATION | post-v0.4 | post-v0.4 | — | `review:L11` |
| L11-10 | Hosted proxy-config cache keying | Low (hosted) | WS-ISOLATION | post-v0.4 | post-v0.4 | — | `review:L11` |
| L11.M-1 | Plugins and `pythonpath` are controlled by LoadPolicy | Critical (hosted) | WS-ISOLATION | M1a | open | — | `review:L11-verif` |
| L11.M-2 | Document cleanup never offers ledger, include or sidecar files | Medium (data loss) | WS-ISOLATION | M5 | open | — | `review:L11-verif` |
| L11.M-3 | Secret references are resolved by an injected resolver, scoped per workspace | Critical (hosted) | WS-ISOLATION | M3 | open | — | `review:L11-verif` |
| L11.M-4 | IMAP verifies server certificates | security | WS-SECURITY | M6 | open | — | `review:L11-verif` |
| L11.M-6 | The background reconciliation load goes through LoadPolicy | Critical (hosted) condition | WS-ISOLATION | M1b | open | — | `review:L11-verif` |
| L11.M-7 | Outbound URLs and hosts are validated (hosted) | Hosted | WS-ISOLATION | post-v0.4 | post-v0.4 | — | `review:L11-verif` |
| L11.M-8 | localStorage is namespaced by workspace | Low | WS-ISOLATION | M2 | open | — | `review:L11-verif` |
| L11.M-9 | Diagnostics and env files are root-relative | Low | WS-BACKEND-CORE | M2 | open | — | `review:L11-verif` |

### 3.13 Doubles lookup (47)

Each double is closed with its canonical row, in the same milestone. Ref: the double's own lens report, as for its series.

| Double | Canonical | Milestone |
|---|---|---|
| L1-01 | I4 | M1a |
| L1-08 | I11 | M4 |
| L1.M1 | I3 | M1b |
| L1.M2 | I2 | M1b |
| L1.M3 | I10 | M1b |
| L1.M4 | I9 | M1b |
| L1.M5 | I4 | M1a |
| L2-01 | I1 | M4 |
| L2-02 | I7 | M7 |
| L2-13 | L2-05 | M2 |
| L2.M-3.1 | I8 | M7 |
| L3.M-1 | L3-07 | M7 |
| L3.M-2 | L3-04 | M4 |
| L3.M-3 | L3-03 | M5 |
| L3.M-4 | L3-03 | M5 |
| L3.M-5 | L3-02 | M5 |
| L3.M-6 | L3-02 | M5 |
| L3.M-7 | L3-05 | M4 |
| L3.M-8 | L3-05 | M4 |
| L3.M-11 | L7-02 | none (M8 verifies absence) |
| L4.M1 | L4-05 | M6 |
| L5-16 | I8 (also L3-10) | M7 |
| L5.M3 | L5-07 | M1a |
| L5.M4 | L5-02 | M7 |
| L5.M6 | L5-11 | M8 |
| L6-09 | L1-02 | M1a |
| L6-13 | L4-12 | M6 |
| N3 | L6-01 | M2 |
| N5 | L6-16 | M6 |
| L7.M2 | L7-03 | M8 |
| L7.M3 | L7-03 | M8 |
| L8-21 | L2-04 | M7 |
| L8-22 | L2-09 (also L5-21) | M7 |
| L8.M-3 | L3-02 | M5 |
| L8.M-17 | L8-23 | M7 |
| L9-19 | L3-08 | M4 |
| L9.M-2 | L9-13 | M2 |
| L9.M-4 | L9-09 (also L4-10) | M4 |
| L9.M-8 | L9-09 | M4 |
| L10-03 | I6 | M2 |
| L10-11 | L1-04 | M2 |
| L10-17 | L2-05 | M2 |
| L10.M-3 | L11-08 | M2 |
| L11-01 | L10-01 | M2 |
| L11-04 | L1-11 | M9 |
| L11-05 | L1-11 | M9 |
| L11.M-5 | L10-01 | M2 |

---

## 4. Invariant enforcement

Each item builds the check that turns one invariant from "pending" into "enforced" (`PROCESS.md`; `review:20 §2`). On day one the tree is empty, so no invariant has a running check yet; every invariant starts pending with the item below. "Enforced" means at least one non-skipped, non-`fixme`, non-`xfail` tagged test or check in T1 (T2 for INV-20). The milestone is the one by which the check must run; CI fails at that milestone's exit if the invariant is still pending. Milestones follow the milestone plan (`review:27` §1–13, App. B); where the plan does not name one, the milestone that builds the capability was chosen (agent). The invariant texts live in `INVARIANTS.md`.

| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0001 | Build enforcement for INV-01: the D1 corpus with apply/inverse property tests, contract-step tests and the old-app oracle comparison before promotion | WS-WRITE | M1b (agent) | open | `review:20 §2` |
| BL-0002 | Build enforcement for INV-02: Beancount-generated vectors run on both sides, plus an architecture test that booking, tolerance, grammar and price semantics are not reimplemented. S0024 wrote the vectors (`corpora/vectors/`: transaction tolerance and residuals with the frontend port's verdict, balance-assertion tolerance, account and currency grammar incl. custom roots and Unicode, the price map, `dcontext` precision, booking per method), their generator `scripts/oracle/gen_domain_vectors.py` and keep-honest test `scripts/oracle/tests/test_domain_vectors.py` (spec `design/specs/kernel/vectors.md`). M1a still owes: the engine wrappers behind `beancount_port` passing them (a test in `engine/` reading `vectors.json`), the architecture test that only `beancount_port` imports Beancount (BL-0041), and then INV-02's "Enforced by" and status (owner's text). The frontend tolerance port and the main sidecar's PriceIndex must pass them when they are built (WS-KERNEL) | WS-KERNEL | M1a (agent) | open | `review:20 §2`, `S0024` |
| BL-0003 | Build enforcement for INV-03: the parity corpus (Beancount and Fava example ledgers; custom roots, globs, plugins) loads and round-trips through the overlay loader; a ledger with an encrypted (`.gpg`/`.asc`) file is refused with a plain message, never partly loaded (ADR-0059), including a root file that is itself encrypted (the D1 write corpus covers included encrypted files only, S0020) | WS-WRITE | M1a (agent) | open | `review:20 §2` |
| BL-0004 | Build enforcement for INV-04: the layout test (no state outside its declared place) plus the verifier checklist | WS-BACKEND-CORE | M2 | open | `review:20 §2` |
| BL-0005 | Build enforcement for INV-05: the atomic-publish swap test and the versioned-view contract golden | WS-MIRROR | M1a (agent) | open | `review:20 §2` |
| BL-0006 | Build enforcement for INV-06: lint bans on float money, the `Money` type brand, and written-scale goldens | WS-KERNEL | M1a (agent) | open | `review:20 §2` |
| BL-0007 | Build enforcement for INV-07: lint ban on UTC "today" and naive datetimes, plus tests run under a `TZ` west of UTC (S0009 lint covers `new Date()`, `Date()`, `Date.now()` and their `globalThis.`/`window.` forms in scripts and templates; aliasing such as `const D = Date` is not caught) | WS-KERNEL | M1a (agent) | open | `review:20 §2` |
| BL-0008 | Build enforcement for INV-08: the strict recipe schema validator and the catalog registry test | WS-RECIPES | M7 | open | `review:20 §2` |
| BL-0009 | Build enforcement for INV-09: the injection test corpus (markdown, payee and metadata values), the CSP header test and the SQL hardening tests; chart-formatter and BQL tests follow in M7 (N1, L8-20, L11-07) | WS-SECURITY | M2 | open | `review:20 §2` |
| BL-0010 | Build enforcement for INV-10: the cache registry test (every cache declares its bound and eviction); the 54k memory regression check joins with the baselines of BL-0020 | WS-BACKEND-CORE | M1a (agent) | open | `review:20 §2` |
| BL-0011 | Build enforcement for INV-11: the globals/state-registry allowlist test; the isolation matrix with its workspace axis follows in M2 (L11-08). Done in S0017 | WS-ISOLATION | M0 | done | `review:20 §2` |
| BL-0012 | Build enforcement for INV-12: import-linter contracts and the frontend boundaries lint. Backend S0006, frontend S0018 | WS-BACKEND-CORE | M0 | done | `review:20 §2` |
| BL-0013 | Build enforcement for INV-13: proposal binding tests (tamper, replay, stale preview) | WS-AI | M9 | open | `review:20 §2` |
| BL-0014 | Build enforcement for INV-14: the import corpus with bucket counts and the double-commit test | WS-IMPORT | M6 | open | `review:20 §2` |
| BL-0015 | Build enforcement for INV-15: a lint/grep test that no code generates account names, plus the "New account…" acceptance flow | WS-UI | M4 | open | `review:20 §2` |
| BL-0016 | Build enforcement for INV-16: acceptance flows and component tests for preview, Cancel focus and backup on destructive actions | WS-UI | M2 | open | `review:20 §2` |
| BL-0017 | Build enforcement for INV-17: migration framework tests (idempotent, dry-run, backed up, never write newer per workspace) | WS-LIFECYCLE | M3 | open | `review:20 §2` |
| BL-0018 | Build enforcement for INV-18: jsx-a11y lint, axe in the acceptance tests and the token contrast test, at WCAG 2.2 AA (known pending) | WS-UI | M11 | open | `review:20 §2` |
| BL-0019 | Build enforcement for INV-19: the generated-artifact diff check and the doc-impact check. Diff check S0000; the doc-impact check is the verifier's folded-in doc check (`.claude/agents/verifier.md`), recorded in S0019 | WS-DOCS | M0 | done | `review:20 §2` |
| BL-0020 | Build enforcement for INV-20: the nightly perf suite compared with a stored baseline, plus the user-facing absolutes (known pending) | WS-QA | M11 | open | `review:20 §2` |
| BL-0021 | Build enforcement for INV-21. S0022 wrote the goldens (`corpora/ledgers/inv21/`: TransactionId incl. ordinals, reorder and cross-file moves, custom root names, empty and odd `source_account` values, `k:` ids; the content-hash input; metadata JSON; the key inventory `keys.toml`, each key read forever or passed through) and their test (`scripts/oracle/tests/test_inv21_goldens.py`, spec `design/specs/mirror/inv21-goldens.md`). M1a still owes: the mirror build and TransactionId resolver passing these goldens (a test in `backend/` reading `goldens.json`), the old-workspace fixtures (a v0.2.2 install's folder and config), and then INV-21's "Enforced by" and status (owner's text) | WS-KERNEL | M1a (agent) | open | `review:20 §2`, `S0022` |
| BL-0022 | Build enforcement for INV-22: a fault-injection test per write-contract step | WS-WRITE | M1b (agent) | open | `review:20 §2` |
| BL-0023 | Build enforcement for INV-23: the T0 privacy scanner, plus owner-reviewed anonymization of fixtures. Scanner S0000 (+ detect-secrets S0016). Left (S0019 verifier F1, F2, F4): `check_staged.py` skips its statement check for data-like files inside `corpora/` and `fixtures/`; the MANIFEST review date is not validated and nothing stops an agent from writing a row (owner review is recorded, not enforced); the anonymization script does not exist yet. Before the first real export arrives | WS-QA | M0 | open | `review:20 §2` |
| BL-0024 | Build enforcement for INV-24: the acceptance-harness port and data-dir checks and the local-tier path guard | WS-QA | M0 | open | `review:20 §2` |
| BL-0025 | Build enforcement for INV-25: tests that "forget" leaves the folder byte-identical and that demo replacement refuses a real workspace, a demo manifest at another path and a symlinked demo folder | WS-LIFECYCLE | M3 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0026 | Build enforcement for INV-26: the adoption corpus byte-identity test and the lock-interleaving test against the packaged v0.2.2 build on all three OSes | WS-LIFECYCLE | M3 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0027 | Build enforcement for INV-27: the raw-tar folder round trip, the layout test, the path-field schema test and the portability-assessment corpus; the Export/Import archive round trip waits for the archive (`post-v0.4`) | WS-LIFECYCLE | M3 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0028 | Build enforcement for INV-28: the route-scope test, the two-tab acceptance flow and the workspace middleware test | WS-ISOLATION | M2 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0029 | Build enforcement for INV-29: open-failure tests per phase (registry bytes unchanged) and an architecture test that no open path calls a ledger creator | WS-BACKEND-CORE | M2 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0030 | Build enforcement for INV-30: the open/close ×N byte-identity test and a secret-pattern scan of `.finzytrack/` | WS-LIFECYCLE | M2 | open | `review:20 §2`, `review:23 §7.2` |
| BL-0031 | Build enforcement for INV-31: the cache registry test per workspace; the A→B→A ×20 memory check at 54k joins with the baselines of BL-0020 | WS-BACKEND-CORE | M2 | open | `review:20 §2`, `review:23 §7.2` |

## 5. Other items

| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0032 | Strict CSP on the web app and the desktop window (`script-src 'self'`, no inline script; a hash for the pre-paint theme script). This row has no review finding ID. | WS-SECURITY | M2 | open | `review:99 §1.3`, `review:27 App. A` |
| BL-0033 | Rust licence check: wire cargo-about 0.9.2 and cargo-deny 0.20.2 into `scripts/check_licences.py` and `THIRD_PARTY_NOTICES` when `shell/` lands. Done in S0010 with `cargo metadata` (the shipped closure on every target); cargo-about collects the licence texts at bundling | WS-QA | M0 | done | `review:F §2 item 9` |
| BL-0034 | T1 process checks not yet built: `tests-fail-on-base`, verifier head-SHA binding, owner-approval quote check, golden-change justification check (PROCESS.md §3, §5, §8) | WS-QA | — | parked | `review:F §3.5`, `ADR-0058` |
| BL-0035 | Add detect-secrets 1.5.0 (entropy checks) to the T0 privacy scanner once a pinned Python dev environment exists; today the scanner is pattern-only, stdlib. Done in S0016 (`scripts/secrets-scan/`, rule `secret-entropy`) | WS-QA | M0 | done | `review:27 §1(d)` |
| BL-0036 | Protected-file guard: refuse agent edits that loosen `design/INVARIANTS.md`, type-checker strictness, import-linter contracts, `.claude/settings.json`, `.githooks/*`, CI workflows or `scripts/check_*.py` without an owner-approval quote in the slice journal; plus a SubagentStop check that a verifier report has all required sections | WS-QA | — | parked | `review:E §3.3`, `ADR-0058` |
| BL-0037 | CI workflow `ci.yml` grows with each package: backend (ruff, pyright, pytest, import-linter, deptry), frontend (eslint, tsc, vitest, knip, Playwright Chromium + WebKit), shell (cargo check, clippy); then `nightly.yml` and `packaged.yml`. Backend job: S0006; frontend job: S0009; shell job: S0010 (fmt, clippy), S0012 (unit tests); `packaged.yml` (three-OS build, install, `--smoke-test` and its must-not-pass run) and `nightly.yml`: S0012; GitHub runs them by hand before each release (ADR-0060) | WS-QA | M0 | done | `review:27 §1(d)` |
| BL-0038 | PROCESS.md no-drift checks (review:20 §10b): generate the gate table from the hook/CI config and the agent roster from `.claude/agents/`; a check that every kept IMPROVEMENTS entry names a PROCESS.md section | WS-QA | — | parked | `review:G R-9`, `ADR-0058` |
| BL-0039 | Capability map: link each `cut` and `deferred` row to its ADR (`adrs` field), then make `check_capabilities.py` require it. Done in S0015 (27 rows) | WS-QA | M0 | done | `review:G F-11` |
| BL-0040 | Tighten `check_invariants.py`: detect skip/xfail/fixme on the tagged tests, count only T1 runners (not pre-commit), require evidence for `review-only`, and guard the owner-owned rule text with a hash that only an owner-approved change updates | WS-QA | — | parked | `review:G F-8, F-9`, `ADR-0058` |
| BL-0041 | Implement the two-process backend of ADR-0054 in M1a: `engine/` environment and lockfile, `contracts/engine-protocol.schema.json`, `engine_client` port, supervision, per-environment architecture tests and licence allowlists; measure the protocol overhead per write and per large BQL result with the S4 harness | WS-BACKEND-CORE | M1a | open | `ADR-0054` |
| BL-0042 | Guard hook parser: text inside quoted heredocs and backticks within quoted strings is split as if it were commands (false positive: a doc edit that mentions the push-gate command was refused). Parse with a real shell tokenizer (e.g. bashlex) or skip quoted heredoc bodies; add meta-tests. S0014: a lexer failed review (round 1); round 2 (single-quoted heredoc bodies in exact command shapes, commit 483b900) is parked unverified and not active; the quoted-string half is BL-0054. Parked by the owner: "BL-0042 stays parked unless I ask." (2026-10-25) | WS-QA | M0 | parked | `S0001 retro` |
| BL-0043 | Frontend WebKit-baseline lint (Safari 17, ADR-0007 amendment): ban APIs newer than the baseline (global `Temporal`, `ReadableStream.from`, async stream iteration, ...), e.g. with eslint-plugin-compat and a browserslist of `safari >= 17`; set `minimumSystemVersion` 14.0 in the Tauri config when `shell/` lands | WS-UI | M2 | open | `ADR-0007`, `S0001` |
| BL-0044 | Add the GPL version 3 text to the repo (`LICENSE-GPL-3.0`) and ship both licence texts in every bundle, matched to the process each covers (ADR-0054, README licence section). The text is in the repo since S0010; shipping both texts per bundle is packaging work (M11) | WS-QA | M11 | open | `ADR-0054` |
| BL-0045 | Oracle harness: add the D1 step-2b outside-span byte check, so divergences the printer cannot see (an untargeted `@@` rewritten as `@`, comments inside entries, formatting) are reported and classified; today they surface only as the file-level comment count | WS-QA | M1b | open | `S0002` |
| BL-0046 | Oracle harness: first T-local run on copies of the owner's ledgers (folder from an environment variable, report with counts and case IDs only), once the owner provides the copies | WS-QA | M1b | open | `S0002` |
| BL-0047 | Oracle harness: attribute Beancount errors that follow from a tagged legacy defect (e.g. a rename on a glob-include ledger leaves "unknown account" errors in the never-rewritten glob files, I2) so such cases are classed rather than UNEXPLAINED; until then rename comparisons use ledgers without glob includes | WS-QA | M4 | open | `S0002` |
| BL-0048 | Intent table T1: check each vocabulary key against the directive it belongs on (review:28 §4.2: `reason` only on a `close`, the deposit keys only on an Assets `open`, ...), so a row cannot list a key for the wrong entry | WS-QA | M4 | open | `S0004` |
| BL-0049 | Capability rows gain a `writes` field (`ledger`, `config`, `none`), required by `check_capabilities.py`; the intent-table T1 check then fails when a `writes = "ledger"` row is cited by no intent row and no "not built" line (the mechanical form of ADR-0057's population sweep) | WS-QA | M1b | open | `ADR-0057` |
| BL-0050 | Licence check: pip-licenses reports trove-classifier names ("BSD License", "MIT License", "Apache Software License") that never match the SPDX allowlist, and "BSD License" does not say which BSD. Add a reviewed per-package SPDX mapping (package, version, SPDX id, where it was read) to `licences.allow.toml`, required before the first runtime dependency lands. The same mapping covers two gaps the S0006 verifier found: packages that `uv export` lists behind a platform marker (e.g. `colorama ; sys_platform == 'win32'`) are bundled on another OS but absent from the CI venv, so today the check fails closed on them and never reads their licence (F1); and `-e <path>` lines are dropped, so an editable or vendored path dependency would be bundled unchecked (F7) | WS-QA | M1a | open | `S0006` |
| BL-0051 | JS licence notices: list what the Vite build actually ships (its module graph, e.g. a rollup licence plugin) instead of license-checker's `--production` tree, which includes optional peers that are never bundled (Vue's `typescript` peer shows as a pending Apache-2.0 warning today) | WS-QA | M2 | open | `S0009` |
| BL-0052 | EP-24 for Cargo: a T1 check that every `Cargo.lock` entry from crates.io was published at least 14 days before the commit that adopted it (or carries the security exception's journal reason); Cargo has no `minimumReleaseAge`. Done in S0013 (`scripts/check_cargo_age.py`; it found three crates S0010 adopted hours early, recorded as an escape) | WS-QA | M0 | done | `ADR-0050` |
| BL-0053 | Licences of the packaged Python runtime: list the libraries python-build-standalone links into each target's CPython (OpenSSL, SQLite, libffi, zlib, xz, bzip2, mpdecimal and so on; Tcl/Tk is trimmed out, from the release's per-build metadata) in `THIRD_PARTY_NOTICES` and ship their texts in the bundle; today only CPython itself is listed (S0012) | WS-QA | M11 | open | `ADR-0056` |
| BL-0054 | Guard false positives left after S0014: guarded words in a single-quoted `git commit -m` or `gh --body` value, and backticks inside single quotes split as commands (e.g. `sed 's/x/`cmd`/'`). Only with a design that removes the risk class (exact shapes), not a general parser (S0014 verifier round 1) | WS-QA | M1a | open | `S0014` |
| BL-0055 | Guard gaps found by the S0014 verifier, open before it: flags word-split out of a substitution (`git commit -m x $(echo --no-verify)`); redirects to `/dev/tcp/…:8001`; writes to `.git/config` setting `hooksPath`; redirect targets of git commands are not checked | WS-QA | M0 | open | `S0014` |
| BL-0056 | Frontend layer map: resolve Vite query imports (`?url`, `?raw`, `?worker`) by their path without the query, so `checkUnknownLocals` stops refusing them; until then they are refused on purpose (S0018 round-2 F1). In the first slice that imports an asset | WS-FE-DATA | M2 | open | `S0018` |
| BL-0057 | Mirror DDL snapshot (`contracts/mirror-ddl.sql`, `mirror-views.md`) and per-table checksums over the corpus ledgers, generated from the M1a mirror build; a test fails when the built DDL (base tables, `r_*` views, compatibility views) differs from `design/specs/mirror/surface.toml`, or when a view column is anything but a plain column reference (affinity must survive) | WS-MIRROR | M1a | open | `S0021` |
| BL-0058 | User docs (`reference/querying-data.md`, docs `v0.4` branch): document the `r_*` surface and the compatibility names from the mirror spec, fix the differences from the code listed in `design/specs/mirror/README.md`, remove the `latest_prices` section | WS-MIRROR | M7 | open | `S0021` |
| BL-0059 | TransactionId is not unique: a copied entry keeps its legacy `id` (and `id:` with no value reads as `None` on every such entry), so two entries can share an id, as in v0.2.2; so can a hash-shaped legacy id (before v0.1 `transaction_id` was the content hash) and an identical entry's content fallback id (both pinned by the INV-21 goldens, `ids-legacy`). The M1b write planner must address an entry by (TransactionId, entry_hash) and refuse when that still matches more than one entry; the mirror and API must not assume uniqueness (no UNIQUE index, no id-keyed maps) | WS-WRITE | M1b | open | `S0022` |
