# Roadmap: from an empty tree to v0.4.0

## Orientation
- **What this is:** the order in which v0.4.0 is built on `next`, milestone by milestone, with what the owner can do at the end of each, what must be true to leave it, and how far real ledgers may be used. It decides nothing about the product; it sequences decisions recorded in the ADRs.
- **Source:** ported on 2026-10-25 (S0007) from the milestone plan review:27, and corrected for decisions made after it was written: Vue 3 as the frontend (ADR-0053); Beancount in its own engine process (ADR-0054); python-build-standalone, not PyInstaller, and Linux as AppImage + .deb (ADR-0056); the pilot's slice count (ADR-0057). This file summarises; it binds nothing by itself. The ADRs decide, and review:27 keeps the binding milestone exits in full detail (ADR-0046), with the per-milestone lists of backlog IDs, acceptance flows and statecharts; cite it as `review:27 §n`. Where this summary differs from an ADR or from review:27, the summary is wrong and is fixed; review:27's statements that a later ADR superseded (listed above) are read as amended by that ADR.
- **What lives elsewhere (not repeated here):** which capability belongs to which milestone: `design/capabilities.md` (the `milestone` column; its `M1` rows split into M1a and M1b per ADR-0046); which backlog item: `design/backlog.md`; G1 and the M1 split: ADR-0046; release scope and gate: ADR-0043; clearance numbers: ADR-0014 and PROCESS.md §10; process lanes: ADR-0044.
- **Keeping it current:** a slice that finishes an item in §1 updates §1 in the same commit; a slice that changes the order or a milestone's exit cites an ADR and edits §3–§5. Milestone sizes are relative (S < M < L < XL, an XL about two to three times an L), not dates.
- **Invariants that shape the order:** INV-01 and INV-22 (no write capability before the write-safety contract, so nothing writes before G1), INV-24 (real ledgers only as copies until promotion), INV-17 (never write a newer format, so no format bump before M3).

## 1. Where we are (updated 2026-10-28)

**Milestone M1a, Read core and workspace-format core (since 2026-10-26).** M0 closed on 2026-10-26 (owner: "Agreed, close M0 on this run plus the fix"; record `design/journal/M0-exit.md`). In M1a so far: the ledger engine as its own process (S0035), beanquery in the engine (S0037), the engine in the product: started at boot by the sidecar, in the packaged build beside it, in the acceptance harness (S0038; its packaged `--smoke-test` proven on macOS, Linux and Windows wait for the next manual CI run); the structured log with default-deny redaction (S0039, BL-0088); the engine supervision as a statechart, with every engine start and stop in the log (S0040, BL-0089); the cross-process failure-mode spec, each row held to its test or its open backlog row (S0041, BL-0090; the gaps it found are BL-0094 to BL-0097, for M2); from the area review of the engine and backend core (2026-10-26), its F3: the caller's time limit covers sending a request, and no pipe wait in the engine client is unbounded, so a frozen engine cannot hang a call or quit (S0042). Nothing runs as an app yet beyond an empty window. Done: the area review of the engine and backend core by a fresh reviewer (PROCESS.md §4 altitude 2; owner, 2026-10-26: "Yes, schedule it after BL-0089 and BL-0090"), on 2026-10-26: its F3 landed in S0042; its F1/F2 were decided by the owner in `design/inbox/answered/20261026-engine-concurrency-and-cancel.md`, a decision for parallel engines that the owner superseded on 2026-10-27 by one engine doing one job at a time (ADR-0064). Also decided on 2026-10-27: one running Finzytrack at a time (S0044, ADR-0062) and one open workspace at a time, with every unfinished save recovered before any workspace opens (S0045, ADR-0063; INV-32). Done since, in the owner's order of 2026-10-27: the engine-jobs spec, draft 5 (`design/specs/engine-jobs.md`, "Engine jobs and saves"), whose golden-review loop the owner ended after three rounds, handing the remaining edge cases to the model (IMP-0038, since replaced by IMP-0045); a convergence audit of the repo against ARCHITECTURE.md §0 by a fresh reviewer (owner: "Yes, add the audit between step 3 and the model run"), its fixes, and a check that refuses the abandoned designs' terms in the current-state documents (S0047, `scripts/check_retired_terms.py`); the TLA+ model built from the spec (`formal/tla/engine-jobs/`, 84c8b8c9). Next in M1a: the engine-jobs slice goes to code now (BL-0109; area review F1/F2, ADR-0054 amendment), cut into six pieces: the first built on 2026-10-28 (S0053: the queue's decisions as a pure table, the engine lock that every engine holds and the sidecar waits for at start, the TLA+ model's code map checked both ways); the client's §5.5 changes (S0057, built on 2026-10-29: no time limits, Cancel and retire as no stops, the engine's own process group drained before the reap, `close()` without waiting, the `-s -S -B -P` command with the bytecode directory, protocol version 2 with the commit-point gate) and the scheduler shell (BL-0150's second slice, S0058), the save and recovery (BL-0151), R4 and the reads (BL-0152), create and copy (BL-0153), the switch, quit and boot (BL-0154, M2); the review of the model against the spec and the owner's long runs go on beside it, never before it, and their findings become tests or backlog rows unless they are a High data-safety issue (BL-0110; PROCESS.md §3 "Designs checked by a formal model"; owner, 2026-10-29: "Yes to Reflection proposals 1, 2, 3, 4, 7, 8, 10 and 12.", IMP-0045). Independent of the engine-jobs slice, one implementer at a time: the API skeleton with the offline OpenAPI export (S0046, built on 2026-10-27: the app factory, the envelope, the error registry and its edge translation, the workspace gate with its three refusals, the route-scope and contract checks, `/health`, `contracts/openapi.json` and its generated TypeScript types under `check_generated`; not yet served), then serving it from the sidecar (S0048, BL-0105, built on 2026-10-28: uvicorn in `app.main`, failure-modes M7's stop window closed, `/health` measured at a 4.6 ms maximum during repeated 54k loads in another process; re-measured with the read core, BL-0114). Then the read core: `beancount_port`, the overlay load and `LoadPolicy` against the domain vectors (S0049, BL-0002, built on 2026-10-28: the single gateway with its load stages reachable only from the overlay load, the overlay replica equal to Beancount's loader on the D1 corpus with encrypted files refused, the `LoadPolicy` seam (its desktop body allows everything: v0.4.0 has no trust prompt, a ledger's plugins run as Beancount runs them, ADR-0040 amendment "no trust prompt on desktop in v0.4.0"; a later prompt is BL-0156, post-v0.4), the grammar and tolerance wrappers passing every engine vector, INV-02 enforced; not yet called over the protocol), then the mirror build (S0050, built on 2026-10-28: every table and view of the surface spec written into a fresh SQLite file from one overlay load, checked against `surface.toml` with the DDL snapshot `contracts/mirror-ddl.sql`, the INV-21 goldens and the `balance` vectors passing through it; 1.1 s of build after a 1.3 s load at 54k entries; the `commodities` rows wait for the owner's ISO list, `design/inbox/20261028-iso-currency-list.md`, BL-0120), then the workspace-format core's first piece (S0051, built on 2026-10-28: the manifest `.finzytrack/workspace.json` with its reader, `LineageId` and `WorkspacePath`, its generated JSON Schema and golden fixtures; the extended fingerprint of a ledger set, recorded by the overlay load from the bytes it parsed and observed again on disk in about 3 ms at 54k; the plugin sources in it wait for R4, BL-0124, and the persisted `ledger_version` for the publish, BL-0125), then the first piece of mirror reads and the QueryRunner (S0052, built on 2026-10-28: in the main sidecar, the read-only open of a mirror file given by its path, the typed reads of the mirror's `meta` and the load's errors, and ADR-0016's hardened runner for user SQL, its confinement attacks refused with nothing written; on the 54k mirror, opening and confining a connection takes 0.2 ms and typical queries 12 to 37 ms; its next pieces are the routes in M2 and BL-0122's indexes), then the atomic publish with its formal model (BL-0113); the protocol overhead and write-path measurement (BL-0082); at M1a's end the process re-measurement (ADR-0058).

Done in M0:
- `next` from an empty tree; T0 and T1 gates (privacy scan, staged-file guard, invariant, capability, generated-artifact diff and licence checks); `design/` seeded; agents, skills and guard hooks (S0000). Their remaining parts are listed under "Left" below.
- The macOS spikes, with S13 deciding Vue and S1 deciding python-build-standalone (S0001; ADR-0053, ADR-0056); the licence inventory that led to the engine split (ADR-0054).
- The differential-oracle harness for three mutators (S0002); the legacy API tests run out of process, as reference reading (S0003).
- The per-mutator intended-change table, the first of M1's specs (S0004).
- The pilot's process ADR and the first slice count (S0005, ADR-0057); the backend skeleton (S0006); this roadmap (S0007); verification scaled to risk (S0008, ADR-0058).
- The package skeletons with their CI gates (BL-0037): backend (S0006), frontend (S0009), the Tauri shell (S0010), and the packaged three-OS build where the empty window passes `--smoke-test`, with `nightly.yml` (S0012). GitHub CI runs by hand before each release (ADR-0060).
- The Cargo 14-day check (BL-0052, S0013); cut and deferred capability rows cite their ADR (BL-0039, S0015); detect-secrets in the privacy scan (BL-0035, S0016); INV-11 enforced by the globals test and state registry (BL-0011, S0017); INV-12 enforced on the frontend too, by the boundaries lint (BL-0012, S0018); BL-0019 closed on its existing mechanisms (S0019).
- Review findings now fail their milestone's exit run while open (S0023); L1-06 and L9-18 closed on S0006 and S0009.
- M1's D1 write corpus: the hand-written nasty cases with their checks, including the encrypted-file refusal (S0020; the generated 5k/54k ledgers come with the demo generator, the example ledgers with BL-0003).
- M1's mirror surface spec: every table and column at M1a, the `r_*` views and the v0.2.2 compatibility names, with its check (S0021; owner decisions of 2026-10-25 applied; the DDL snapshot and checksums come with the M1a build, BL-0057).
- M1's INV-21 goldens: TransactionIds, content hashes and metadata JSON for ledgers v0.2.x wrote, taken from v0.2.2's own code and re-derived from the spec, and the inventory of every metadata key a released version wrote (S0022; the M1a code must pass them and the old-workspace fixtures are still to come, BL-0021).
- M1's Beancount domain vectors: transaction tolerance and residuals (with the frontend port's verdict), balance assertions, account and currency grammar, the price map, display precision and booking per method, each an input with Beancount 3.2.0's output, kept equal to a fresh run of the committed generator (S0024; the M1a engine code must pass them, BL-0002).
- The demo generator skeleton: the fragment interface, the background household, sizes demo, 5k and 54k, the build step `backend/scripts/gen_demo.py`, and its gates (0 Beancount errors at every size, the same bytes under any clock or time zone, one assertion per fragment); with it L10-18 closed (S0027; the release-build wiring, seed-dashboard and oracle-total gates and the multi-file variant are BL-0063).
- The P1 integrity tests, written now and marked `fixme` (review:27 §1(d)): one test per P1 finding (backend pytest, vitest for the frontend findings) and the six integrity flows A1, A2, A4, A7, A10, A13 as Playwright specs (rows BL-0064…BL-0070); each `fixme` names an open backlog row with its milestone, so that milestone's exit run fails while the test is pending (S0029).

Closed at the M0 exit (2026-10-26; `design/journal/M0-exit.md`), as they stood:
1. BL-0042 is parked by the owner ("BL-0042 stays parked unless I ask", 2026-10-25); the guard gaps found in S0014 (BL-0055) closed by S0028 for the forms named there; deliberate-evasion variants are BL-0062, parked with BL-0042. Since S0054 (2026-10-29; IMP-0045, proposal 2): BL-0042 is done (heredoc bodies as data), BL-0062 is removed, BL-0055 stays done with its anti-bypass refusals removed, and the stricter heredoc and subshell check is BL-0155, parked until a real escape. The checks on the process's own paperwork (BL-0034, BL-0036, BL-0038, BL-0040) are parked until evidence asks for them (ADR-0058).
2. The invariant enforcement due by M0: BL-0024 closed by S0030 (the acceptance harness's port and data-dir checks, the local-tier path guard). BL-0023's gaps closed by S0026 (its anonymization script moved to BL-0060, M6).
3. No review finding due by M0 is open (`check_invariants.py` fails an M0-exit run while one is, S0023): L9.M-7 closed by S0025, L10-18 by S0027.
4. The rest of review:27 §1(d) that M0's exit needs: the misses of the first seeded-defect run (run 1 done: 72 plants, 69% caught, journal S0032; S0032 made a fixme pass only while its planned module is missing, so a buggy first L4-03, L9-01 or I5 is red; S0033 closed the rest, BL-0079: CLI exit-code tests of the privacy scan, each hook's code pinned, three privacy rules (UPI addresses, short account numbers after an account word, statement excerpts in prose and messages), no skip in an architecture test, and a push check that refuses an uncited deferral of a backlog item; three verifier rounds, landed 2026-10-26) and a green manual full CI run (BL-0076, closed on the second manual run plus a Linux test fix; `FT_MILESTONE_EXIT=M0` passes). The red-team session on the gates is done (BL-0074): its must-fix findings are closed by S0031 (the push gate checks the pushed commits and every blob they add, lint at push, no skipped enforcement test, the oracle tests at push), the privacy denylist is the owner's question (`design/inbox/20261025-privacy-denylist.md`). The P1 integrity tests are written (S0029); the acceptance-suite harness is built (S0030: per-worker temp root, fresh demo, the main sidecar on its own port with a frozen clock, the LLM stub, `TZ` west of UTC plus a Kolkata project, Chromium and WebKit, axe and console, request and LLM failures as hard failures; the parts that need M2's API are BL-0071, streaming LLM replies BL-0072).

Next: the rest of **M1a**, then **M1b** (write contract), then **G1**, your go/no-go.

## 2. The milestones at a glance

| # | Milestone | Size | What the owner can do at the end | Real ledgers? |
|---|---|---|---|---|
| M0 | Foundations | XL | Nothing in an app. CI and gates, `design/`, agents, oracle, M1's specs, demo skeleton, acceptance harness, an empty Tauri window | No |
| M1a | Read core + workspace-format core | L | Nothing in the UI. Ledger engine process, mirror, overlay load, platform ports, workspace format with the extended fingerprint | No |
| M1b | Write contract | L | Nothing in the UI. Watch the contract refuse bad writes on copies of your own ledgers (local tier) | Copies only |
| **G1** | **Go/no-go checkpoint** | — | You decide: go, go with changes, or no-go | — |
| M2 | Shell, boot, opening a ledger (read-only) | L | Install a dev build, open an existing ledger, switch ledgers, see errors and notices | **Read-only** |
| M3 | Workspace lifecycle and legacy adoption | L | Create a workspace, try the demo, make a test copy, bring your v0.2/v0.3 install in by copy | Read-only on your real books; writes only in new and copied workspaces |
| M4 | Accounts | L | Account tree with period balances; create, edit, close, rename, merge, delete; balance assertions; statements | Promoted account writes only |
| **G2** | Confidence checkpoint | — | A short report after the first replace, delete and multi-entry writes | — |
| M5 | Transactions and documents | L | Find, edit, bulk-edit, delete and undo transactions; attach documents | Promoted transaction writes only |
| M6 | Import and reconciliation | XL | Import CSV, XLS, OFX, email and AI-read statements; one-shot merchant categorization; "why doesn't my balance match?" | Promoted import commit only |
| M7 | Dashboards, Analyze, Budgets | XL | Every general dashboard, the widget library, SQL and BQL, budgets | Promoted budget writes only |
| M8 | Investments and prices | XL | Holdings, gains, prices, quick actions, booking methods, "compare with broker", one-upload sync | Promoted investment and price writes only |
| M9 | Assistant | L | The AI assistant: analysis, rule and dashboard proposals, ledger proposals, reconcile from chat | Through promoted writes only |
| M10 | Settings and completion | M | Every settings section, Report a problem | Yes, every write promoted |
| M11 | Release hardening | L | An auto-updating release candidate on three OSes; the 4-week dogfood window | Yes, as your only finance app |
| M12 | Release | M | v0.4.0 is out; docs `v0.4` merged | — |

**Rough size (ADR-0057):** about 180–270 slices for the rest of M0 through M12 (170–256 for M1–M12 alone; ADR-0057's table labels the total "M1–M12" but includes M0's remaining 12–16), very roughly 550–1,350 agent hours. An order of magnitude only; the G1 evidence pack replaces it with the pace measured through M1b.

**Fixed points the whole plan assumes** (each an owner decision):
- **Release cut line** (ADR-0043): every capability blocks the release except four, deferred past v0.4.0: the workspace export/import archive, broker formats beyond CAS, the beta update channel, Finzytrack AI mode. Bring-your-own cloud and local AI providers stay in v0.4.0.
- **Platforms** (ADR-0007, ADR-0056): Apple-silicon macOS (macOS 14+, ad-hoc signed, not notarized), Windows 10/11 x64 (unsigned), Linux x64 (AppImage and .deb). Identifier `com.finzytrack.desktop`.
- **Stack:** Vue 3 frontend (ADR-0053); a GPLv3 main sidecar that never imports Beancount, and a GPL-2.0-only ledger engine process that holds it (ADR-0054); Python 3.13 with Beancount 3.2.0 (ADR-0004), moving to 3.14 with 3.2.3 in the first slice after v0.4.0.
- **Agents run locally; CI is deterministic only; direct commits on `next`** (ADR-0045).
- **Shortest path to a releasable v0.4.0, not early visibility** (owner steering, review:27 "Owner steering" 1). A re-order that shows things earlier is welcome only if it costs no time.

## 3. Rules every milestone inherits

### 3.1 Using real ledgers safely (the dogfood rules)
"Book of record" means the ledger you treat as your real books; until release it stays the legacy install's ledger unless you move it.
- **R1. Contract first.** No write capability exists before G1. Every write passes the write-safety contract, each step failing closed (INV-01, INV-22).
- **R2. Unpromoted writes only on test copies.** A copy-adopted workspace is a normal workspace (real books). An unpromoted mutator runs only on an explicit test copy (CAP-WS-TEST-COPY); the guard checks every file the plan writes, and otherwise refuses with `UNPROMOTED_MUTATOR_ON_REAL_LEDGER`.
- **R2a. Clearance by kind of edit** (ADR-0014): Add 50 writes / 1 week; Change or delete one entry 100 / 2 weeks; Many entries or files 30 / 2 weeks including crash tests; twice both numbers for a mutator with no old-app comparison. You sign one clearance page per milestone.
- **R3. Git under the book of record** while dogfooding; you review the diff of every write.
- **R4. Reconciliation before the next write** (a real Beancount load compared with the overlay load), in every mode and never switched off (ADR-0008 amendment, ADR-0064). It runs right after the reply and completes before the next write to that workspace is accepted; any mismatch blocks writes, and while dogfooding it stops dogfooding of that mutator.
- **R5. Never both apps writing one ledger.** v0.3 has no external-edit check and rewrites whole files. v0.4 has no Share mode (ADR-0062): your legacy ledger comes in by Copy, and the old app keeps its own file.
- **R6. Agents never touch real data, and never stop, start or call your running app** (INV-24; they ask you to stop it, ADR-0065). Copies of real ledgers live outside the repo and run only in the local tier, which prints counts and case IDs only.
- **R7. Downgrade note.** If v0.2/v0.3 rewrites a ledger v0.4 edited, comments and splices are lost.

### 3.2 Exit gates every milestone includes
Not repeated per milestone (review:27 §0.5): every slice met the definition of done (PROCESS.md §3); the milestone's release-blocking capability rows are `built` (or `cut` with the absence verified; the rows for ADR-0043's four deferred capabilities are built if time allows and never hold a milestone exit), with tagged tests and a user-doc page; demo fragments for its rows; generated artifacts current; T1 and the full acceptance suite green in Chromium and WebKit, with no flow of this or an earlier milestone still `fixme`; a green manual full CI run (T1 and T2) at the milestone exit, including performance and memory against the baseline (ADR-0060); an **area review** by a fresh reviewer; the **UX red team** session on every vertical with UI; a **reflection cycle** with `IMPROVEMENTS.md` entries; the milestone's journal entry with owner time and process metrics; refresh the architecture explainer (link in `design/README.md`) from the current documents (IMP-0047).

**Platforms (ADR-0061):** an exit needs **macOS green** (the T0 pre-push gate on the owner's Mac at the exit commit, and the acceptance suite run locally on macOS through the harness, recorded in the milestone's journal entry) and a green manual CI run, where a test that fails only on Linux or Windows, is not cheap to fix and is not a safety test is marked `fixme` due M11 with a backlog row citing ADR-0061 (safety, write-path, INV-26 and integrity-core tests are never deferred). From M1b on, every exit also needs the engine's write-contract tests green on a Windows runner. The Linux and Windows sessions run in M11, before its dogfood window.

**Run before declaring an exit:** `FT_MILESTONE_EXIT=Mx python3 scripts/check_invariants.py` (with Mx the milestone, e.g. `M0`) must pass: no invariant still pending and no backlog item (review finding or `BL-`) still open or in progress that is due by Mx (S0023). The plain pre-push run checks only the backlog's shape. The manual CI run does the same check when started with the `milestone_exit` input (S0031).

**Where the green manual CI run is recorded:** in the milestone's journal entry (the run's link and the commit it tested), not as a backlog row. A backlog row due by the milestone whose subject is the exit run would keep that run red while the row is open, since the run itself checks for open rows. BL-0076 is the M0 exception, written before this rule: it is closed on a green run started without `milestone_exit`, and the exit check then runs locally (S0036).

**Lanes** (ADR-0044): the full process for anything that writes the ledger; finance and security red teams once per area unless a change alters what a number means or adds an input; a light lane for read-only screens and catalogue entries, whose user docs are written at milestone end. Cross-cutting lens reviews run at G1, G2, after M8 and at M11 only, each with a spot audit by a fresh context and the seeded-defect catch rate (ADR-0044, ADR-0045); one whole-system review runs at M11.

## 4. The milestones

Each entry: what is built, the exit beyond §3.2, and the dogfood rule. Detail per milestone: review:27 §1–§13.

### M0: Foundations (XL)
- **Built:** the tree and its gates (T0, T1; T2 and the packaged matrix as packages arrive); `design/`; agents and skills; the oracle harness; **only M1's specs** (intent table, D1 write corpus, mirror surface and DDL, INV-21 goldens, Beancount vectors); the demo generator skeleton; the acceptance harness; the P1 integrity tests as `fixme`; the package skeletons; the empty Tauri window; the spikes; the pilot.
- **Exit:** CI fails on planted bugs (I2, I5, L4-03, L9-01); the empty window passes `--smoke-test` on three OSes; every spike has a recorded verdict (S13 decided Vue); the pilot ADR is recorded (ADR-0057); a red-team session on the gates; a first seeded-defect run (≥ 20 plants, ≥ 4 classes).
- **Dogfood:** no. You keep using v0.3.0 from the legacy worktree.

### M1a: Read core and workspace-format core (L)
- **Built:** the **ledger engine** as its own process with its own environment, lockfile and licence list, the engine protocol schema and the main side's `engine_client`, with supervision (ADR-0054, BL-0041); in the engine: `beancount_port`, the overlay load and `LoadPolicy` (ADR-0010), the mirror build and atomic publish (ADR-0015); on the main side: mirror reads and the hardened QueryRunner (ADR-0016), platform ports and adapters, the first domain types, the workspace-format core (layout, manifests, extended fingerprint, persisted `ledger_version`), the API skeleton with the offline OpenAPI export.
- **Exit:** the process re-measured (ADR-0058: product against process code, building against review-and-fix time per slice); D2 equivalence exact on the corpus; mirror swap test with zero read errors; QueryRunner attacks refused; one load per operation, no resident ledger (INV-10); `/health` answers under 100 ms during a 54k load; the protocol overhead per write and per large BQL result measured.
- **Dogfood:** no.

### M1b: Write contract (L)
- **Built:** in the engine, splicing and edit producers (ADR-0009), the contract's steps as a fail-closed pipeline (ADR-0008), the writer, the write-intent journal and recovery (ADR-0012), atomic swap replace (ADR-0013), the promotion machinery and the oracle classifiers (ADR-0014); the Hypothesis crash model (ARCHITECTURE.md §8.2), with the code mapping of the TLA+ model's actions; a test-only edit producer; the write-cost measures of ADR-0008's amendment (the garbage collector paused during a write, the step-1 parse reused by the full load, the re-check after the reply), then the save measured at 54k; a bounded cache of the edited file's parse is built only if that measurement misses 5 s (ADR-0008 parse-cache amendment, BL-0112). No user-facing mutator.
- **Exit:** an injected failure in any contract step leaves the disk byte-identical; the TLA+ model green (its small tier in T1, its medium and large tiers run by the owner; ARCHITECTURE.md §8.3); the Hypothesis model green; D2 equivalence also on copies of your ledgers (local tier); the oracle comparison with every divergence classed, non-comparable or explained; data-safety, finance and security red teams; a spot audit of one sampled slice by a fresh context; the engine's write-contract tests green on a Windows runner in the manual CI run (ADR-0061; from M1b on, at every exit).
- **Dogfood:** copies only, in the local tier. The new code never opens your originals.

### G1: Go/no-go (ADR-0046)
**Go criteria:** a green manual full CI run at each milestone exit and at G1 (ADR-0060); at least 200 old-vs-new comparisons, none unexplained; under 5% unjustified refusals on copies of your books; a write takes ≤ 1 s at demo size and ≤ 5 s at 54k entries, mirror update included (until the screen reflects the change; ADR-0008 amendment); at least 80% of planted bugs caught. No fixed time-to-finish ceiling: the evidence pack shows the measured pace and projected time, and you judge it.
**You get:** does the contract hold; does it agree with the old app where the old app is right; what it refuses and why; what a write costs; whether the process works; the re-estimated size of M2–M12.
**Options:** go; go with named changes; no-go (fall back to incremental work on the v0.3.0 code, moving the contract and engine in first; the gates, specs, corpora and harness carry over).

### M2: Shell, boot, opening a ledger, read-only (L)
- **May start after M1a**, in parallel with M1b (nothing here writes a ledger).
- **Built:** boot assessment and the workspace open and switch machinery (registry, containers, workspace-addressed API, legacy-root probe, one v0.4 instance per app root (BL-0100, ADR-0062), one open workspace at a time with the switch (BL-0102) and every unfinished save recovered before any workspace opens (BL-0103, ADR-0063)); the frontend foundations (generated API client, Vue Query with workspace-scoped keys, scoped storage, the logger, `ShellPort`, `src/domain` money and dates, the shadcn-vue primitives and the app components such as confirm, dialogs, unsaved guard, fields, safe markdown); the real sidecar supervisor and shell plugins; the Boot, Workspace-switch and Sidecar statecharts.
- **Exit:** the route-scope test for every route; the globals registry lists every stateful part; axe clean; security red team on safe markdown, CSP, path-taking endpoints and the LAN guard.
- **Dogfood:** **read-only.** You may open your real ledgers; opening never writes a ledger file (INV-30). A ledger your old install uses can only be brought in by Copy (ADR-0062), which arrives in M3; until then, open a copy of it that you made yourself.

### M3: Workspace lifecycle and legacy adoption (L)
- **Needs G1** (it creates ledger files through the contract).
- **Built:** the lifecycle executor and migration framework (plan, consent, backup, apply, record; never write newer, INV-17); content packs; demo workspace replacement (INV-25); legacy detection and **adoption by Copy**, the only mode, refused while the old app holds its lock (ADR-0020, ADR-0062); one v0.4 instance per app root is enforced earlier, in M2 (BL-0100); Bring-in, secrets; test copies; the first mutators `create_ledger_set` and `seed_demo`.
- **Also an exit item:** the concrete recipe schema v3, settled here so M7's engine work can start early.
- **Exit:** adoption leaves the old install byte-identical, run twice and interrupted; every migration idempotent and dry-runnable; a newer workspace refused with "restore backup of run X"; the legacy-lock detection test (INV-26); data-safety and security red teams.
- **Dogfood:** read-only on the book of record; you may adopt your real install **by Copy** after the adoption corpus passes on a copy of it (R5).

### M4: Accounts (L)
- **Built:** the full Ledger Profile; period balances as the kernel measures M7 reuses; account statements with running balances; open, edit, close, reopen; balance assertions with pad pairing; `move_postings` and rename, merge, delete (ADR-0049), crash-atomic across files through the journal; account documents; money formatting per commodity.
- **Exit:** promotion records per kind of edit (R2a); period balances checked against `bean-query`; data-safety red team (renames across includes, names inside narrations and comments); finance red team (future-dated entries, custom roots, multi-currency); first vertical at parity, so the UI-consistency and accessibility lens runs here.
- **Dogfood:** yes, for promoted account mutators, under R1–R7.

### G2: Confidence checkpoint (not a kill point)
After M4's first replace, delete and multi-entry promotions: promoted mutators so far, real-use volume, any reconciliation mismatch, any unexplained oracle divergence. If a problem class shows up, later verticals pause until it is understood. Also the second cross-cutting review (finance kernel, data safety).

### M5: Transactions and documents (L)
- **Needs:** the `@@` printing question settled (L3-03) before its edit slices.
- **Built:** filtering and deep links; the single-entry edit that merges the request into the parsed source entry (typed metadata, `@@`, cost dates and lot labels kept); bulk edit with an operation log; delete and undo; the grid with keyboard navigation; the edit-buffer statechart; the documents store and the orphan scan.
- **Exit:** promotion records; data-safety red team (pushtag regions, CRLF, inline comments, `@@` round trips); finance red team (multi-currency, cost and price edits, tolerance).
- **Dogfood:** yes, for promoted transaction mutators.

### M6: Import and reconciliation (XL)
- **Built:** the import pipeline (read, adapt, normalize, resolve the anchor account, statement arithmetic, match, reconcile report, suggest; ADR-0027); persistent review sessions; categorization in tiers (your rules, one-shot merchant memory derived from the ledger, fuzzy match, a tuned ML tier, an optional AI tier; ADR-0027); OFX, email and AI readers; the LLM gateway; reconcile mode (ADR-0028); the optional, consented legacy-metadata cleanup as the last slice (ADR-0026).
- **Exit:** the categorization benchmark (one-shot accuracy about 100% for exact merchant memory; accuracy and coverage per tier and per source); importing the same file twice writes nothing; data-safety, finance and security red teams.
- **Dogfood:** yes, for the promoted import commit. A real monthly import and one reconciled month are the best test.
- **May split** into two sub-milestones after sizing (tabular and matcher, then email, AI and OFX).

### M7: Dashboards, Analyze and Budgets (XL)
- **Needs:** recipe schema v3 (M3 exit). The recipe engine backend may run alongside M4–M6, reading only from the mirror.
- **Built:** the strict recipe validator and step interpreter with the `ledger` step's measures (ADR-0029); the versioned `r_*` views, `dsum` and `price_asof` (the public SQL surface, INV-05); BQL steps; `PriceIndex`; budgets as directives; the dashboards UI with ECharts; the recipe migrations. v0.2.2 table and function names keep working through 0.4.x, `lots` included (ADR-0048).
- **Exit:** promotion records for budget mutators; every seed dashboard agrees on total assets for any date; finance and security red teams.
- **Dogfood:** yes. Reads are safe; budget writes once promoted.
- **May split** into M7a (engine, seeds, Analyze) and M7b (budgets).

### M8: Investments and prices (XL)
- **Built:** holdings and value as of a date from Beancount's booking; simple gains and income; CDs; every booking method Beancount books, with previews and a lot picker; quick actions; price sources (Yahoo, AMFI, I-bonds) with history and manual prices; "compare with broker" and one-upload sync with CAS (ADR-0031, ADR-0032); the investment seed dashboards. XIRR, CAS replace and statement import are out on purpose.
- **Exit:** promotion records; finance red team at full strength, checked against `bean-query`; data-safety red team on booking-method changes that re-book history.
- **Dogfood:** yes, for promoted investment and price mutators; one real broker statement or CAS through one-upload sync, on a copy first.

### M9: Assistant (L)
- **Built:** one assistant over the slices' capabilities (ADR-0033); conversations per user; proposals bound to the workspace, conversation and preview hash, confirmed through the same plans as the UI; a session that read untrusted content cannot apply anything. No new ledger mutator.
- **Exit:** the tamper cases rejected; security red team at full strength (prompt injection through attachments, emails, payees, metadata); finance red team on analyst answers.
- **Dogfood:** yes, within the promotion state of the mutators it calls.

### M10: Settings and completion (M)
- **Built:** the complete Settings page with one unsaved guard; operating currencies (a ledger write); backups, logging, server options, notices, About; hand-edited settings YAML; Report a problem with a scrubbed bundle.
- **Exit:** every mutator in the intent table promoted or explicitly not built with its ADR; the oracle comparison retired (write-path stage 6).
- **Dogfood:** yes, with every mutator promoted. You may move your book of record to v0.4 if you choose; never both apps writing.

### M11: Release hardening (L)
- **Built:** the updater statechart with a signed manifest (stable channel; beta deferred); `release.yml` for three OSes (ad-hoc signing on macOS, sidecar and engine signed too); the user-facing performance and accessibility enforcement (INV-20, INV-18); the whole-system review; the Linux and Windows sessions on real machines before the dogfood window (ADR-0061: platform bugs, installers, platform UX, the `fixme` rows due M11).
- **Exit:** every acceptance flow green, none `fixme`, in both engines and on packaged builds; all four red teams once more; the spot audit with the seeded-defect catch rate. The dogfood window starts.
- **Dogfood:** the release candidate is your only finance app for **4 weeks**, with one month-end reconciliation and one broker sync, zero reconciliation mismatches, and the ledger under git.

### M12: Release (M)
The release gate (ADR-0043): every release-blocking row built; every mutator promoted; the real-ledger write corpus on copies; the dogfood window passed; the upgrade matrix from v0.2.0–v0.2.2 (adoption twice and interrupted, old install byte-identical and still starting); packaged builds and the updater on three OSes; architecture checks with no exemptions; the mutation-score floor; complete release notes, including everything removed relative to v0.2.2; legal advice on the licence applied. Then `next` merges into `main`, tag `v0.4.0`, docs `v0.4` merges into docs `main`. If the dogfood window surfaces a write-path fix, the window restarts.

## 5. Sequencing

**Critical path:** M0 → M1a → {M1b → G1, M2} → M3 → M4 → G2 → M5 → M6 → M7 → M8 → M9 → M10 → M11 (dogfood window) → M12. The write path dominates: every write vertical waits for its promotions. Clearance runs once per kind of edit (four kinds), so its waiting periods start as soon as the first mutator of a kind exists and overlap the next milestone's build.

**Can run alongside:**

| Track | Alongside | Why it is safe |
|---|---|---|
| M2 (shell, boot, open) | M1b | Read-only; needs only M1a |
| Packaging pipeline and Tauri shell work | every milestone from M0 | Independent of features |
| Recipe engine backend | M4–M6, after recipe schema v3 (M3 exit) | Reads only the mirror; its UI still lands in M7 |
| Prices backend and CAS/positions adapters | M5–M7 | Pure parsers and fetchers; price writes wait for M8 |
| LLM gateway | M4–M5 | Needed by M6; reads only |
| Clearance waiting periods | the next milestone's build | Calendar time on copies |

**Cannot run alongside:** no write capability before G1; no transaction replace before L3-03 is settled; no format bump before M3's never-write-newer rule; investments after Import's matcher and the `ledger` step; the assistant's ledger proposals after the mutators they call are promoted; stage 6 after the last vertical's promotions.

## 6. Open technical points (settled in specs, not owner calls unless they hit the rubric)
- `@@` printing (L3-03): an own posting renderer or `replace_token` on the price span. Before M5.
- Write cost for very large single-file ledgers (the open part of spike S4): the save at 54k is measured in M1b first (BL-0112); a bounded cache of the edited file's parse, on disk in a safe format and never Beancount's pickle cache, is built only if that measurement misses the 5 s target; only if it then still misses does the owner revisit the number for that case (ADR-0008 amendments).
- The concrete recipe schema v3. By M3's exit.
- The mutation-score floor for the ledger and domain code: proposed after M1's first measurement, fixed by M11.
- Whether M6 and M7 split into sub-milestones, judged by the measured pace.
- The categorization ML model family, chosen by the benchmark inside M6.
- Owner prerequisites for M11 (ask early): the updater signing key, generated once with two offline backups; the CI secrets for it.
