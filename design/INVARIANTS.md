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
- **Enforced by:** planned: domain vectors generated from Beancount; in the engine, an architecture test that only `ledger.beancount_port` imports Beancount (BL-0041); in the main sidecar, `backend/.importlinter` and `backend/tests/architecture/test_contracts.py` (no Beancount import at all, ADR-0054). *(Enforcement list updated with the owner's approval, 2026-10-25.)*
- **Status:** pending (BL-0002, by M1a)

### INV-03: Beancount parity
- **Rule:** No Beancount feature is narrowed. If Beancount loads a ledger, Finzytrack handles it: custom roots, every booking method, globs, plugins. Where Beancount itself refuses something, say so.
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
- **Enforced by:** planned: XSS payload tests; SQL/BQL hardening tests; the CSP.
- **Status:** pending (BL-0009, by M2)

### INV-10: No resident parsed ledger; every cache is bounded
- **Rule:** No resident parsed ledger. Every in-memory cache is per user, evictable and has a bound. Bounds are configuration values sized per deployment (generous desktop defaults), not development targets.
- **Why:** Keeps a memory-frugal hosted option open and removes snapshot-drift bugs on desktop (owner, 2026-10-24).
- **Enforced by:** planned: a cache registry test (every cache declares its bound and eviction); a memory regression check at 54k.
- **Status:** pending (BL-0010, by M1a)

### INV-11: Services hang off containers
- **Rule:** Services hang off `UserContainer` or `WorkspaceContainer`; limiters, the job queue and the AI chat store hang off `UserContainer`. There is no module-level mutable state outside the allowlist. *(Amended by review:23 §7.2.)*
- **Why:** Module globals leak state between users and workspaces (review:18).
- **Enforced by:** `backend/tests/architecture/test_globals.py` (no module-level or class-level value outside a short immutable allowlist, no module-level expression, `global`/`nonlocal` or memoizing decorator, unless registered; stated limit: an alias of stdlib state such as `os.environ` passes), `backend/src/app/platform/state_registry.py` (the registry: process-wide names, at most one, and container-owned parts, each with owner, bound and eviction). Planned: the isolation matrix with a workspace axis for the containers' rule (L11-08, M2).
- **Status:** enforced

### INV-12: Layering
- **Rule:** Routers translate HTTP, use cases orchestrate, the domain is pure. On the frontend, no business rule sits in a template or component render; logic sits in domain modules, hooks or machines.
- **Why:** Logic in the wrong layer was the main source of duplicated, drifting rules (review:10, review:19).
- **Enforced by:** planned: import-linter contracts; ESLint boundaries rules.
- **Status:** pending (BL-0012, by M0)

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
- **Enforced by:** planned: eslint-plugin-jsx-a11y (eslint-plugin-vuejs-accessibility if spike S13 keeps Vue); axe in acceptance tests; a token contrast test.
- **Status:** pending (BL-0018, by M11)

### INV-19: Docs do not drift
- **Rule:** Generated docs are never hand-edited. Every user-visible change updates the user docs in the same slice.
- **Why:** Docs that duplicate code drift from it (review:20 §7).
- **Enforced by:** `scripts/check_generated.py`, `scripts/tests/test_check_generated.py` (generated files carry a header and match a fresh generation). Planned: the doc-impact check in the verifier.
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
- **Rule:** Agents and tests never touch the owner's real data or running backend. Tests run on copies in isolated data dirs and ports; real-ledger copies run only in the local tier.
- **Why:** An agent mistake must never reach the owner's books (owner, 2026-10-24).
- **Enforced by:** `.claude/hooks/guard_paths.py`, `.claude/hooks/guard_bash.py`, `scripts/tests/test_guards.py`, .claude/settings.json (deny rules), `scripts/oracle/launch_legacy.py` (refuses the live data folder), `scripts/oracle/copies.py` (the copy guards), `scripts/tests/test_oracle_copies.py`, `scripts/oracle/tests/test_harness.py` (copy containment, source unchanged). Planned: the acceptance harness port and data-dir checks; the local-tier path guard.
- **Status:** enforced

### INV-25: Workspace operations never delete user files
- **Rule:** "Forget" removes the registry row and local caches only. The one replacement in the system is the demo, identified by `kind: demo` **and** the `realpath` of the app's demo path with no symlinks.
- **Why:** Generalizes the demo safety rule (review:22 §1).
- **Enforced by:** planned: forget leaves the folder byte-identical; demo replacement refuses a real workspace, a demo-kind manifest at another path, and a symlinked demo dir.
- **Status:** pending (BL-0025, by M3)

### INV-26: A legacy install keeps working
- **Rule:** v0.4 never writes under a legacy root **except** the shared ledger set (root, includes, sidecar), its documents folder and `.write.lock`. A shared ledger is written only while holding every recorded legacy lock, with the same primitive the legacy app uses.
- **Why:** The owner runs v0.2/v0.3 alongside v0.4; the old app must keep working (review:23).
- **Enforced by:** planned: the adoption corpus test (legacy config, mirror, backups, import sessions and every non-shared ledger byte-identical after adopt and after a v0.4 session); a lock-interleaving test on all three OSes against the packaged v0.2.2 build.
- **Status:** pending (BL-0026, by M3)

### INV-27: The folder is the workspace, and it is never hostage
- **Rule:** A workspace is a self-contained, portable folder: tar it on desktop, import it into a hosted version, and it just works, and the reverse. Everything that isn't a rebuildable cache or machine-local working state is inside the folder; the folder holds no absolute paths and no secret values (only their names). A workspace that passes `assess_portability(hosted)` round-trips desktop → hosted → desktop with a byte-identical ledger set and tool state.
- **Why:** The user's data is never held hostage (owner, 2026-10-24).
- **Enforced by:** planned: a raw-tar round trip of a workspace folder in CI; a layout test; a path-field schema test; the `assess_portability` corpus. The Export → Import archive round trip joins when the archive ships (deferred past v0.4.0, ADR-0043). *(Enforcement list amended with the owner's approval, 2026-10-25; the rule is unchanged.)*
- **Status:** pending (BL-0027, by M3)

### INV-28: Workspace-addressed
- **Rule:** Every workspace-scoped request names its workspace in the path. There is no server-side "active workspace", and a request, response or write can never act on a workspace other than the one named. `{ws}` resolves only in the caller's registry.
- **Why:** A shared "active ledger" pointer caused wrong-ledger writes (review:10 I6, review:15 L10-01); two tabs must not retarget each other.
- **Enforced by:** planned: a route-scope test; a two-tab acceptance flow; a middleware test; a `meta.workspace_id` client assertion; the isolation-matrix IDOR row.
- **Status:** pending (BL-0028, by M2)

### INV-29: Validate before recording
- **Rule:** "Last opened" and every registry change happen only after `assess_workspace` returns `READY`/`WARMING`. A missing ledger is never created implicitly.
- **Why:** Recording a failed open, or creating a ledger for a typo, lost users' place and data (review:10 I6, review:19 L8-15).
- **Enforced by:** planned: open-failure tests per phase (registry bytes unchanged); an architecture test that no open path calls a ledger creator.
- **Status:** pending (BL-0029, by M2)

### INV-30: Opening never writes the folder
- **Rule:** `.finzytrack/` holds no secrets, no caches and no per-machine fields. Opening, switching and closing a workspace N times leaves the folder's bytes unchanged. Tool-state rewrites (migrations included) happen only on the user's save or consent; the lineage id is never rewritten automatically. **Stated exception:** write-journal recovery on open, with a one-line notice (review:D1 §13.1).
- **Why:** Writes on open cause sync conflicts, leaked keys, git noise and id ping-pong between copies (review:23).
- **Enforced by:** planned: an open/close ×N byte-identity test; a duplicate-lineage test; a secret-pattern scan of `.finzytrack/`; a layout test.
- **Status:** pending (BL-0030, by M2)

### INV-31: Only the active workspace is touched
- **Rule:** Closing a workspace's last reference releases its caches and handles. Cache bounds are configurable limits, global across workspaces and generous on desktop, never fixed development targets.
- **Why:** INV-10 at workspace granularity; memory-frugal hosting (review:23).
- **Enforced by:** planned: a memory regression check A→B→A ×20 at 54k against the baseline (flat RSS); the cache-registry test.
- **Status:** pending (BL-0031, by M2)
