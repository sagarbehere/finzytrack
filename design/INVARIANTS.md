# Invariants

The owner-owned rules of Finzytrack. Every invariant has an ID, the rule, a "why" line, its enforcement and a status. `scripts/check_invariants.py` parses this file in T0 pre-push and T1.

**Status values** (design/process/PROCESS.md, "Invariants"):
- `enforced`: at least one non-skipped, non-`fixme`, non-`xfail` test or check tagged with the ID exists and runs in T1 (T2 for INV-20). The **Enforced by** line lists its files; each file must exist and mention the ID.
- `pending (BL-nnnn, by Mx)`: the enforcement does not exist yet. The backlog item builds it and must be open; CI fails at milestone Mx's exit if the invariant is still pending.
- `review-only`: enforced only by the verifier's review. Rare, and always stated.

**Changing this file.** Adding or tightening an invariant: an agent proposes it with evidence; the owner approves it in a batch of escalations. Weakening or removing one always needs the owner. Every change gets an ADR and an entry in `design/process/IMPROVEMENTS.md`.

Origin: accepted by the owner as a starting point on 2026-10-24 (review:20 §2), with the workspace invariants and amendments of review:23 §7.2; the workspace invariants are numbered INV-28–31 (owner, 2026-10-24; ADR-0003). The amended texts of INV-04, INV-11 and INV-17 replace the originals (review:F §3.5).

---

### INV-01: Every ledger write passes the write-safety contract
- **Rule:** Every ledger write passes the D1 write-safety contract: the semantic diff equals the intent, no new errors unless acknowledged, the hash/fingerprint check passes, the write is atomic with a backup, and the read-back matches.
- **Why:** Ledger corruption is the owner's top risk, above performance (review:10 D1).
- **Enforced by:** planned: the D1 write corpus; property tests (apply/inverse); the old-app oracle comparison before promotion; fail-closed per INV-22.
- **Status:** pending (BL-0001, by M1b)

### INV-02: Beancount is the oracle
- **Rule:** Beancount is the oracle for booking, tolerance, validation, account grammar and price semantics. Finzytrack never reimplements them.
- **Why:** A second implementation drifts from Beancount and gives wrong numbers silently (review:11).
- **Enforced by:** the domain vectors generated from Beancount, kept equal to it by `scripts/oracle/tests/test_domain_vectors.py` and passed by the engine's own load and wrappers in `engine/tests/ledger/test_vectors.py`; in the engine, only `ledger.beancount_port` imports Beancount (`scripts/check_engine_boundary.py`, run by `engine/tests/architecture/test_boundaries.py`), its load stages are reached only from the overlay load (`engine/tests/architecture/test_load_rule.py`), and the overlay load equals Beancount's loader on the D1 corpus (`engine/tests/ledger/test_overlay.py`); in the main sidecar, `backend/.importlinter` and `backend/tests/architecture/test_contracts.py` (no Beancount import at all, ADR-0054). The frontend tolerance port (L3-09) and the main sidecar's `PriceIndex` (L3-12) pass the vectors when they are built. *(Enforcement list updated with the owner's approval, 2026-10-25; the planned checks built and the status set in S0049, BL-0002.)*
- **Status:** enforced

### INV-03: Beancount parity
- **Rule:** No Beancount feature is narrowed. If Beancount loads a ledger, Finzytrack handles it: custom roots, every booking method, globs, plugins. Where Beancount itself refuses something, say so. *(Stated exception, owner 2026-10-25, ADR-0059: in v0.4.0 a ledger with an encrypted file is refused with a plain message, not read.)*
- **Why:** Finzytrack is a front end over the user's Beancount ledger; ledgers that Beancount accepts must not be second-class (owner, 2026-10-24).
- **Enforced by:** planned: the parity corpus (Beancount and Fava example ledgers).
- **Status:** pending (BL-0003, by M1a)

### INV-04: State placement
- **Rule:** Facts live in the ledger. Tool config and workspace policy live in `.finzytrack/` (app-level settings in `app/`). Caches are rebuildable and live in the cache dir. Working state lives in machine-local dirs keyed by registry key. UI memory lives in scoped localStorage. *(Amended by review:23 §7.2.)*
- **Why:** The owner does not want Finzytrack holding state outside the ledger beyond losable UI memory (owner, 2026-10-24).
- **Enforced by:** planned: a layout test; the verifier checklist.
- **Status:** pending (BL-0004, by M2)

### INV-05: The mirror is a rebuildable cache
- **Rule:** The mirror is a rebuildable cache, published atomically. Its versioned views are the public SQL surface.
- **Why:** Users and dashboards query the mirror; a half-built or unrebuildable mirror shows wrong numbers (review:10 I4).
- **Enforced by:** planned: a mirror rebuild test; the view-contract golden.
- **Status:** pending (BL-0005, by M1a)

### INV-06: Money is exact
- **Rule:** Money is `Decimal` / `TEXT` / string / `Money`. Float appears only at display and in documented aggregation. The written scale is preserved.
- **Why:** Float money loses cents and changes what was written (review:11).
- **Enforced by:** planned: lint bans; the `Money` type brand; golden tests.
- **Status:** pending (BL-0006, by M1a)

### INV-07: Civil dates, never a UTC "today"
- **Rule:** Dates are civil dates in the user's time zone (Temporal on the frontend). There is never a UTC "today".
- **Why:** A UTC "today" moves entries to the wrong day for users west or east of UTC (review:11).
- **Enforced by:** planned: a lint ban; tests run under a `TZ` west of UTC.
- **Status:** pending (BL-0007, by M1a)

### INV-08: Dashboards are recipes
- **Rule:** Dashboards are user-authored, non-executable JSON recipes over self-describing catalogs. SQL is a first-class escape hatch.
- **Why:** Dashboards must never be baked in; user creativity and AI authoring depend on it (owner, 2026-10-24).
- **Enforced by:** planned: the recipe schema validator; the catalog registry test.
- **Status:** pending (BL-0008, by M7)

### INV-09: Data is never markup
- **Rule:** Data is never markup: no HTML or script from ledger, recipe, import or AI content. Query engines are confined.
- **Why:** Ledger, import and AI text is untrusted input (review:18).
- **Enforced by:** the SQL hardening tests of the one user-SQL runner, `backend/tests/mirror/test_query.py` (S0052). Planned: XSS payload tests; BQL hardening tests; the CSP.
- **Status:** pending (BL-0009, by M2)

### INV-10: No resident parsed ledger; every cache is bounded
- **Rule:** No resident parsed ledger. Every in-memory cache is per user, evictable and has a bound. Bounds are configuration values sized per deployment (generous desktop defaults), not development targets.
- **Why:** Keeps a memory-frugal hosted option open and removes snapshot-drift bugs on desktop (owner, 2026-10-24).
- **Enforced by:** planned: a cache registry test (every cache declares its bound and eviction); a memory regression check at 54k.
- **Status:** pending (BL-0010, by M1a)

### INV-11: Services hang off containers
- **Rule:** Services hang off `UserContainer` or `WorkspaceContainer`; the engine job queue and the AI chat store hang off `UserContainer`. There is no module-level mutable state outside the allowlist. *(Amended by review:23 §7.2. Amended on 2026-10-27 with the owner's approval, "Yes, I agree with both" (S0047): the rule's list names only what ADR-0064 kept, the engine job queue, and the registry has no process-wide entry; IMPROVEMENTS.md.)*
- **Why:** Module globals leak state between users and workspaces (review:18).
- **Enforced by:** `backend/tests/architecture/test_globals.py` (no module-level or class-level value outside a short immutable allowlist, no module-level expression, `global`/`nonlocal` or memoizing decorator, unless registered; stated limit: an alias of stdlib state such as `os.environ` passes), `backend/src/app/platform/state_registry.py` (the registry: container-owned parts only, each with owner, bound and eviction; a process-wide entry is refused, ADR-0064). Planned: the isolation matrix with a workspace axis for the containers' rule (L11-08, M2).
- **Status:** enforced

### INV-12: Layering
- **Rule:** Routers translate HTTP, use cases orchestrate, the domain is pure. On the frontend, no business rule sits in a template or component render; logic sits in domain modules, composables or machines. *(Amended with the owner's approval, 2026-10-29, ADR-0066: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it."; "hooks" became "composables", the Vue 3 word for the same thing, ADR-0053.)*
- **Why:** Logic in the wrong layer was the main source of duplicated, drifting rules (review:10, review:19).
- **Enforced by:** `backend/.importlinter`, `backend/tests/architecture/test_contracts.py` (the backend's layers and purity contracts, S0006); `frontend/eslint.boundaries.js`, `frontend/tests/unit/boundaries.test.ts` (the frontend's layer map, S0018). The rule's second sentence (no business rule in a template or component render) stays with the verifier's review (checklist EP-01, EP-12): import rules cannot see where logic sits inside a file. Also out of a static lint's reach, and so review-only: `import(variable)` and `import.meta.glob(...)`.
- **Status:** enforced

### INV-13: AI changes are proposals
- **Rule:** AI changes are proposals: server-bound, previewed and confirmed. A ledger, rule or dashboard change is never auto-applied.
- **Why:** An AI model can be wrong or steered by injected content (review:13).
- **Enforced by:** planned: proposal binding tests (tamper, replay, stale).
- **Status:** pending (BL-0013, by M9)

### INV-14: Import never silently drops or guesses
- **Rule:** Import never silently drops, guesses or defaults. Every row lands in a bucket. Ambiguity goes to the user. Re-import is idempotent.
- **Why:** Silent drops and duplicate commits corrupt the books without anyone noticing (review:12).
- **Enforced by:** planned: an import corpus with bucket counts; a double-commit test.
- **Status:** pending (BL-0014, by M6)

### INV-15: No proposed account names
- **Rule:** Finzytrack never proposes or auto-creates account names.
- **Why:** An invented default account leads to a bad experience at best and a mistake at worst (owner, 2026-10-21).
- **Enforced by:** planned: a lint/grep test; an acceptance flow.
- **Status:** pending (BL-0015, by M4)

### INV-16: Destructive actions are safe
- **Rule:** A destructive action has a preview, safe focus (Cancel), a backup, and undo where possible.
- **Why:** Users must see what a destructive action does before it happens and be able to recover (review:17).
- **Enforced by:** planned: acceptance flows; component tests.
- **Status:** pending (BL-0016, by M2)

### INV-17: Never write a newer format
- **Rule:** Never write a format newer than this app understands, per workspace. Migrations are idempotent, dry-runnable and backed up. A step that raises `min_reader` is gate-class. *(Amended by review:23 §7.2.)*
- **Why:** Workspace folders are opened by older apps on other machines (review:15, review:23).
- **Enforced by:** planned: migration framework tests.
- **Status:** pending (BL-0017, by M3)

### INV-18: Accessibility
- **Rule:** Accessibility meets WCAG 2.2 AA.
- **Why:** The app must work with a keyboard and a screen reader (review:17).
- **Enforced by:** planned: eslint-plugin-vuejs-accessibility (ADR-0053; its recommended rules already run in `frontend/eslint.config.js`, S0009); axe in acceptance tests; a token contrast test.
- **Status:** pending (BL-0018, by M11)

### INV-19: Docs do not drift
- **Rule:** Generated docs are never hand-edited. Every user-visible change updates the user docs in the same slice.
- **Why:** Docs that duplicate code drift from it (review:20 §7).
- **Enforced by:** `scripts/check_generated.py`, `scripts/tests/test_check_generated.py` (generated files carry a header and match a fresh generation); `.claude/agents/verifier.md` (the doc-impact half is review-only: the verifier compares the brief's expected doc changes with the diff, every slice).
- **Status:** enforced

### INV-20: Performance does not regress
- **Rule:** The event loop is never blocked; per-write and per-read times do not regress significantly against the previous baseline; the user-facing absolutes hold (a write takes at most 1 s at demo size and at most 5 s at 54k entries).
- **Why:** The app must stay usable at 54k transactions (owner, 2026-10-24).
- **Enforced by:** planned: the nightly perf suite comparing with the stored baseline.
- **Status:** pending (BL-0020, by M11)

### INV-21: Data contract
- **Rule:** (a) Every key a **released** version wrote that a v0.4 reader needs is read forever: `id`/`transaction_id` and the TransactionId scheme (legacy id, then import key, then content fallback with ordinals), the legacy exact-match refs (`external_id`, `external_id_type`, `ofx_id`), `source_account` for the fallback id and the mirror's source-account column, transaction-level `ofx_memo` as an alias of `memo`, every `import-key` version, and the released vocabulary. (b) Every other key passes through untouched. (c) Only the consented cleanup may remove or rename a key, and only after rewriting any identity it carries into `import-key`. Keys written only by the unreleased v0.3 are not covered by (a).
- **Why:** Users' existing ledgers must keep working after the upgrade (owner, 2026-10-24; review:28).
- **Enforced by:** planned: old-workspace fixtures; fallback-id and metadata-key goldens.
- **Status:** pending (BL-0021, by M1a)

### INV-22: Safety checks fail closed
- **Rule:** If a validator or contract step errors, times out or cannot decide, the write is refused.
- **Why:** A safety check that passes on error is not a safety check (review:D1 §12.2).
- **Enforced by:** planned: a fault-injection test per contract step.
- **Status:** pending (BL-0022, by M1b)

### INV-23: Public-repo privacy
- **Rule:** Nothing from the owner's real ledgers or exports, and no secret, account number, PAN, IBAN or personal amount, enters the repo.
- **Why:** This repo is public, and `design/` grows forever (review:20 §8b).
- **Enforced by:** `scripts/privacy_scan.py`, `scripts/check_staged.py`, `scripts/tests/test_privacy_scan.py`, `scripts/tests/test_check_staged.py`, `.githooks/pre-commit`. Owner-reviewed anonymization of real exports (listed in corpora/MANIFEST.tsv with hash and review date).
- **Status:** enforced

### INV-24: Agents and tests never touch real data
- **Rule:** Agents and tests never touch the owner's real data: they never read the live data folder and never write into the legacy worktree. Tests run on copies in isolated data dirs and ports; real-ledger copies run only in the local tier. Agents never stop, start or call the owner's running app themselves; if it is in their way they ask the owner to stop it (a working rule for agents, not a guard check). *(Amended with the owner's approval, 2026-10-29, ADR-0065: "Why do we even care about the running v0.3 app? You should just ask me to kill the app myself, so you can proceed."; the running-backend part moved from the guard to the agents' working rule.)*
- **Why:** An agent mistake must never reach the owner's books (owner, 2026-10-24).
- **Enforced by:** `.claude/hooks/guard_paths.py`, `.claude/hooks/guard_bash.py` (the live data folder and the legacy worktree; the owner's app is not guarded, ADR-0065), `scripts/tests/test_guards.py`, .claude/settings.json (deny rules), `scripts/oracle/launch_legacy.py` (refuses the live data folder), `scripts/oracle/copies.py` (the copy guards, compared by folder identity and ignoring case; the local-tier guard: real-ledger copies only from the folder named by `FT_LOCAL_TIER_DIR`), `scripts/tests/test_oracle_copies.py`, `scripts/oracle/tests/test_harness.py` (copy containment, source unchanged, `--ledger-dir` only from repo corpora or the local tier), `scripts/acceptance/launcher.py` (the acceptance harness: its backend gets port 0, never the owner's port, and only folders the harness made in its own temp root; only processes it started are stopped), `scripts/tests/test_acceptance_harness.py`, `frontend/e2e/harness/fixtures.ts` (in browser tests, requests from the test's browser contexts reach only the preview server and the run's own backend; WebSockets are refused), `frontend/e2e/harness.spec.ts`, `frontend/tests/unit/e2eHarness.test.ts`. Not covered yet: requests that test code sends itself with `page.request`, `context.request` or plain `fetch` (BL-0071). *(Enforcement list amended with the owner's approval, 2026-10-27: "Replace the line with the accurate list re: inv-24"; the rule is unchanged.)*
- **Status:** enforced

### INV-25: Workspace operations never delete user files
- **Rule:** "Forget" removes the registry row and local caches only. The one replacement in the system is the demo, identified by `kind: demo` **and** the `realpath` of the app's demo path with no symlinks.
- **Why:** Generalizes the demo safety rule (review:22 §1).
- **Enforced by:** planned: forget leaves the folder byte-identical; demo replacement refuses a real workspace, a demo-kind manifest at another path, and a symlinked demo dir.
- **Status:** pending (BL-0025, by M3)

### INV-26: A legacy install keeps working
- **Rule:** v0.4 never writes under a legacy root: it creates, changes and removes nothing there, `.write.lock` included. A legacy ledger comes into v0.4 only by Copy, never shared. Before Copy reads a legacy ledger set, and before a save to a workspace whose ledger files a legacy install's config names, v0.4 tries the write lock of **every** legacy install whose config names those files, once each, without waiting, with the primitive the legacy app uses. Only two outcomes let it go on: "free" (the lock is kept for that read or save) and "absent" (no lock file). "Held", and any other outcome (an error opening or locking the file), refuse the copy or save ("Quit the old Finzytrack first"; fail closed, INV-22) and nothing is written. After a copy read that found a lock file absent, the lock file must still be absent and the sources' fingerprint unchanged, or the copy is discarded and refused. *(Amended with the owner's approval, 2026-10-27, ADR-0062: "I approve the needed changes to INV-26 and ADR-0020". The shared-ledger exception and the rule to write a shared ledger only under every legacy lock are gone with Share.)*
- **Why:** The owner, and users upgrading, may keep a v0.2/v0.3 install working on its own data while they move to v0.4 (review:23); the old app rewrites whole files, so v0.4 never shares a ledger with it (ADR-0062).
- **Enforced by:** planned: the adoption corpus test (legacy config, mirror, backups, import sessions and every legacy ledger file, also one outside the root, byte-identical after adopt and after a v0.4 session, and no file created under the root); the legacy-lock detection test (a lock held from another process the way the legacy app holds it, portalocker 3.2.0 `LOCK_EX`, is reported held and a free one free, neither waits; an error opening or locking the file refuses; a lock file that appears, or a source that changes, during a copy read whose check found it absent refuses the copy; macOS in the unit tests, Linux and Windows in the manual CI run). *(Enforcement list amended with the owner's approval, 2026-10-27, ADR-0062: the lock-interleaving test against the packaged v0.2.2 build narrows to the detection test.)*
- **Status:** pending (BL-0026, by M3)

### INV-27: The folder is the workspace, and it is never hostage
- **Rule:** A workspace is a self-contained, portable folder: tar it on desktop, import it into a hosted version, and it just works, and the reverse. Everything that isn't a rebuildable cache or machine-local working state is inside the folder; the folder holds no absolute paths and no secret values (only their names). A workspace that passes `assess_portability(hosted)` round-trips desktop → hosted → desktop with a byte-identical ledger set and tool state.
- **Why:** The user's data is never held hostage (owner, 2026-10-24).
- **Enforced by:** planned: a raw-tar round trip of a workspace folder in CI; a layout test; a path-field schema test; the `assess_portability` corpus. The Export → Import archive round trip joins when the archive ships (deferred past v0.4.0, ADR-0043). *(Enforcement list amended with the owner's approval, 2026-10-25; the rule is unchanged.)*
- **Status:** pending (BL-0027, by M3)

### INV-28: Workspace-addressed
- **Rule:** Every workspace-scoped request names its workspace in the path, and is served only when that workspace is the one open in this instance (in hosted mode, in this user container) (ADR-0063); a request naming any other registered workspace is refused with `WORKSPACE_NOT_OPEN` and changes nothing. No request finds its target from server-side state, and a request, response or write can never act on a workspace other than the one named. `{ws}` resolves only in the caller's registry. *(Amended with the owner's approval, 2026-10-27, ADR-0063: "reword INV-28 and INV-31 and add INV-32 as proposed"; INV-28's hosted wording: "Yes INV-28 should say the same.")*
- **Why:** A shared "active ledger" pointer caused wrong-ledger writes (review:10 I6, review:15 L10-01); two tabs must not retarget each other: a switch in one tab makes the other tab's requests fail, never land in another workspace.
- **Enforced by:** planned: a route-scope test; a two-tab acceptance flow; a middleware test; a `meta.workspace_id` client assertion; the isolation-matrix IDOR row; a switch test: after A→B, a request naming A, a save included, is refused with `WORKSPACE_NOT_OPEN` and leaves A's and B's files and the registry byte-identical.
- **Status:** pending (BL-0028, by M2)

### INV-29: Validate before recording
- **Rule:** "Last opened" and every registry change happen only after `assess_workspace` returns `READY`/`WARMING`. A missing ledger is never created implicitly.
- **Why:** Recording a failed open, or creating a ledger for a typo, lost users' place and data (review:10 I6, review:19 L8-15).
- **Enforced by:** planned: open-failure tests per phase (registry bytes unchanged); an architecture test that no open path calls a ledger creator.
- **Status:** pending (BL-0029, by M2)

### INV-30: Opening never writes the folder
- **Rule:** `.finzytrack/` holds no secrets, no caches and no per-machine fields. Opening, switching and closing a workspace N times leaves the folder's bytes unchanged. Tool-state rewrites (migrations included) happen only on the user's save or consent; the lineage id is never rewritten automatically. **Stated exception:** write-journal recovery, which runs before a workspace opens and may put any workspace's files back to their state before an interrupted save, with a one-line notice naming that workspace (review:D1 §13.1; ADR-0063). *(Amended with the owner's approval, 2026-10-27, ADR-0063: "I agreed with all your recommendations."; `design/inbox/answered/20261027-inv-32-recovery-rule.md`, relayed by the orchestrator.)*
- **Why:** Writes on open cause sync conflicts, leaked keys, git noise and id ping-pong between copies (review:23).
- **Enforced by:** planned: an open/close ×N byte-identity test; a duplicate-lineage test; a secret-pattern scan of `.finzytrack/`; a layout test.
- **Status:** pending (BL-0030, by M2)

### INV-31: One open workspace, and only it is touched
- **Rule:** An instance (in hosted mode, a user container) has at most one open workspace; only the open workspace accepts requests or runs jobs. Opening another is a switch: it assesses the target first, then quiesces and closes the current workspace, losing and half-applying nothing (ADR-0063). Closing a workspace releases its caches and handles. Cache bounds are configurable limits, global across workspaces and generous on desktop, never fixed development targets. *(Amended with the owner's approval, 2026-10-27, ADR-0063: "reword INV-28 and INV-31 and add INV-32 as proposed"; INV-28's hosted wording: "Yes INV-28 should say the same.")*
- **Why:** INV-10 at workspace granularity; memory-frugal hosting (review:23).
- **Enforced by:** planned: a memory regression check A→B→A ×20 at 54k against the baseline (flat RSS); the cache-registry test; the switch test (at no moment do two workspaces accept requests or run jobs; after A→B no container, cache or handle of A remains); a quiesce test: a save confirmed in A before the switch completes in A, or is refused with A unchanged.
- **Status:** pending (BL-0031, by M2)

### INV-32: Every unfinished save is recovered before any workspace opens
- **Rule:** Before any workspace opens (at start, and at each switch after the current workspace has closed), every write journal of the instance (in hosted mode, of the user container) is recovered by ADR-0012's rule. A journal names every file its save replaced or created by the canonical path resolved when the journal was written, and recovery writes only those paths, each only while it still resolves to itself. A journal that cannot be recovered blocks the open of every workspace whose ledger set uses a file it names, and every copy that would read such a file, and nothing else (ADR-0063). *(Added with the owner's approval, 2026-10-27, ADR-0063: "I agreed with all your recommendations." (option 1 of `design/inbox/answered/20261027-inv-32-recovery-rule.md`, with "every other workspace" changed to "every workspace", as recommended: the journal's own workspace is blocked too). The last item of "Enforced by" was rewritten after the owner's answer on the way out: "Yes, I agree with the keep the files as they are now way out." Both relayed by the orchestrator. The added clause "or which is that journal's workspace moved or copied" was shown to the owner before landing: "Yes, the wording is OK" (2026-10-27, in session).)*
- **Why:** With one open workspace, the only way one workspace can read or overwrite another's unfinished work is a save cut off by a crash. Undoing every such save first makes even a file used by two workspaces harmless: the next open sees the change and rebuilds (ADR-0063).
- **Enforced by:** planned: at start and at a switch, a journal of a workspace other than the target is recovered before the target opens; a journal that cannot be recovered (its folder unreachable, foreign bytes) refuses with `RECOVERY_BLOCKED` only the workspaces and copies that use a file it names, and the others open; a file reached through a symlink made after the save is recovered at the journal's recorded path, and a path that no longer resolves to itself is not written; regression tests for the S0045 verifier's F1 (a cut-off save's created file matched by another workspace's glob) and F-W (a symlink made after open sends a created file into another workspace's folder); a folder whose files an unresolved journal names, or which is that journal's workspace moved or copied, is blocked at every open, Open folder and Locate included; "Keep the files as they are now" resolves it, writing nothing to the ledger files; forget keeps the journal.
- **Status:** pending (BL-0103, by M2)
