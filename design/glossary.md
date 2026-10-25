# Glossary

This file defines the project vocabulary used across `design/`, the code and the tests, in one or two plain sentences per term.

Terms are grouped by area and sorted inside each group. Where a term is decided by an ADR, the ADR is named.

## Ledger and Beancount

- **Account kind.** The root type of an account (Assets, Liabilities, Equity, Income, Expenses), computed from the ledger's own root names, which may be custom (ADR-0024).
- **Anchor posting.** The posting of an imported line on the known Assets or Liabilities account whose statement it came from. It carries the `import-key` (ADR-0026, ADR-0027).
- **Balance assertion.** A Beancount `balance` directive that states an account's balance at the start of a date. Finzytrack uses it as the durable record of "reconciled through date X" (ADR-0028).
- **Booking method.** The rule Beancount uses to pick lots when a holding is reduced: FIFO, LIFO, HIFO, STRICT, STRICT_WITH_SIZE, NONE (AVERAGE is declared but refused by Beancount 3.2). Set ledger-wide or per account (ADR-0031).
- **Canonical entry key.** An entry's printer output with location metadata removed. It identifies "the same entry" before and after a write (ADR-0008).
- **Commodity / currency.** A commodity is any Beancount unit (a currency, a share, a fund unit). A currency is a commodity the Ledger Profile assigns the cash role; it is a role, not a lexical class (ADR-0025).
- **Entry / directive.** One dated item in a Beancount file (transaction, `open`, `close`, `balance`, `price`, `custom`, …).
- **Error key.** The identity of a ledger error that survives line shifts: error type + canonical entry key + message without locations (ADR-0008).
- **Import key (`import-key`).** The one app-written identity key: a space-separated set of `v<N>:<kind>:<ref>` elements on every anchor posting of an imported line. It makes re-import idempotent (ADR-0026).
- **Ledger.** The user's Beancount books: a root `.beancount` file and every file it includes. The ledger holds the facts; Finzytrack never keeps a second copy of them as truth.
- **Ledger set.** The root, its glob-expanded includes, the price sidecar and the documents folder: everything the write contract and the fingerprint consider (ADR-0017).
- **Ledger Profile.** The backend-served summary of ledger facts the UI needs: root names, currencies and their roles, tolerance options, display precision, the unknown account, the metadata vocabulary, the ledger version (ADR-0024).
- **Lot.** A batch of a holding acquired at one cost and date (optionally with a label). Beancount tracks lots; Finzytrack reads them from Beancount's booked inventory as of a date and never computes them itself (ADR-0030, ADR-0031). `lots` is also a v0.2.2 mirror table name kept for compatibility (ADR-0048).
- **Metadata vocabulary.** The documented keys a user may add (for example `asset-class`, `exposure`, `account_number`, `income-type`). Recognised, never required (ADR-0026).
- **Pending.** An imported entry with flag `!` whose anchor's `import-key` elements are all of kind `auth` (an authorization not yet settled). Derived, never stored (ADR-0026).
- **Price sidecar.** The `<stem>.prices.beancount` file next to the ledger root where fetched prices are written; a write-contract member file (ADR-0030).
- **PriceIndex.** The one price lookup with Beancount price-map semantics: as of a date, specific to the quote currency (ADR-0024).
- **Tolerance.** Beancount's rule for how far a transaction may be from balancing, inferred from the written precision. Finzytrack uses Beancount's own computation (ADR-0024).
- **TransactionId.** How Finzytrack addresses a transaction without stored ids: the legacy `id` if present, else derived from the first `import-key` element, else a content hash plus ordinal. Always paired with an `entry_hash` on writes (ADR-0026).
- **Written scale.** The number of decimals a user or file wrote. It is preserved, because Beancount's tolerance depends on it (ADR-0023).

## Write path and data safety

- **Clearance (promotion).** Enabling a mutator for real ledgers after it meets its criteria (corpus, properties, oracle comparison, volume and time per kind of edit) and the owner signs the milestone's clearance page. Until then it runs only on test copies (ADR-0014).
- **Contract (write-safety contract).** The seven fail-closed steps every ledger write passes before any byte reaches disk: parse, semantic diff, full load gate, fingerprint, atomic write with backup, read-back, adopt (ADR-0008). Also called the D1 contract.
- **Corpus.** A curated set of ledgers and inputs used as a test asset: the D1 write corpus (example ledgers, generated 5k/54k ledgers, nasty cases), import fixtures, attack-input corpora, old-workspace fixtures. Real-ledger copies live only outside the repo, in the local tier.
- **D1 / D2.** Review names for the write-path design (D1: the contract and splicing) and the overlay load (D2). Kept as short names in tests and docs (ADR-0008 to ADR-0010).
- **Draft.** One proposed ledger entry under review, with an anchor posting, explicit open slots and provenance. Import, manual entry and AI proposals all produce drafts (ADR-0027).
- **Edit / edit producer / mutator.** An `Edit` is one insert, replace, delete or token replacement in one file span. An edit producer turns a request into edits plus the intended change. A mutator is a user-facing write capability implemented as an edit producer (ADR-0009).
- **Expected divergence class.** A known, classified way the old app's result differs from the new app's (I2, I3, I10, M28, FIX-<id>). Any unclassified divergence blocks clearance (ADR-0014).
- **Extended fingerprint.** The stat-and-hash summary of the whole ledger set (every include, glob matches, absent paths, documents roots). It decides mirror freshness and detects external edits (ADR-0015).
- **Fail closed.** If a safety check errors, times out or cannot decide, the write is refused (INV-22).
- **Intent / intended change.** What a write is supposed to change, stated as data. The contract's semantic diff must equal it exactly (ADR-0008).
- **Intent table.** The per-mutator table of intents, acknowledgeable error keys and old-app expectations; the pilot slice builds it (ADR-0046).
- **Oracle.** A trusted source to compare against. Beancount is the oracle for ledger semantics (INV-02); the old app (v0.3.0, run from the legacy worktree on copies) is the differential oracle for write behaviour (ADR-0014).
- **Overlay load.** A full Beancount load where some files are given as in-memory candidate text instead of disk content, so a write is validated before it reaches disk (ADR-0010).
- **Plan / WritePlan.** The data form of a change: what will be written, with hashes and new error keys, created by a preview and executed only by a confirm (EP-08, ADR-0038).
- **Splicing.** Editing only the exact line span of an entry in its source file, so everything outside it is untouched (ADR-0009).
- **Swap-based replace.** Replacing a file by an atomic swap and hashing the displaced file, so an outside save at the same moment is detected and kept (ADR-0013).
- **Write-intent journal.** A machine-local record, written before a multi-file write, of each file's pre hash, intended post hash and backup, used for crash recovery (ADR-0012).

## Workspaces, mirror and lifecycle

- **App root / app-level state.** v0.4's own machine-local folders from its Tauri identifier: registry, app settings, secrets, per-workspace local state, locks, logs (ADR-0017).
- **Copy / Share (adoption).** The two ways to bring a legacy ledger into v0.4: Copy (default) makes a portable copy; Share lets both apps write one file, gated (ADR-0020).
- **Demo workspace.** The generated, disposable demo, a workspace of `kind: demo` replaced on each release (ADR-0022).
- **External-ledger reference.** A workspace whose ledger stays in its original folder; the absolute path lives only in the machine-local registry, and the workspace is badged not portable (ADR-0017).
- **Legacy install / legacy worktree.** A v0.2.x or v0.3 install (`config/` + `data/`) that v0.4 reads but does not change (INV-26). The legacy worktree is the read-only checkout of the `legacy-final` tag next to the repo.
- **Lineage id / registry key.** The lineage id travels with a workspace folder and identifies "this set of books"; the registry key is the machine-local id of one registry row, which keys caches and URLs (ADR-0017).
- **Lifecycle run.** A planned, consented, backed-up sequence of file writes (create, adopt, migrate), recorded with apply-then-record (ADR-0019).
- **Mirror.** The SQLite projection of the ledger in the cache dir. It is a rebuildable cache, published atomically as generations; outside tools may read it (ADR-0015).
- **`r_*` views.** The versioned, documented SQL surface of the mirror for user queries (INV-05).
- **Phase (boot / workspace).** The state an assessment returns, for example `NO_WORKSPACE`, `RECOVERING`, `LEDGER_UNAVAILABLE`, `WARMING`, `READY` (ADR-0019).
- **Portable.** A workspace whose folder contains everything needed to open it elsewhere, with no absolute paths or secret values; checked by `assess_portability` (ADR-0017, INV-27).
- **Reconciliation.** Comparing a statement (bank) or a positions statement (broker) with the ledger and explaining the whole difference (ADR-0028, ADR-0032). "D2 reconciliation" is a different thing: the background `load_file` check after a write (ADR-0010).
- **Workspace.** A folder with a `.finzytrack/` subfolder plus its ledger set: the unit a user opens, switches and moves (ADR-0017).

## Import and categorization

- **Bucket.** Every input row ends in exactly one bucket (line, rejected with a reason, recognised non-transaction), and the counts are shown (INV-14).
- **Merchant memory.** The categorization tier that learns "this merchant means this account" from the user's own categorized ledger, after one confirmation (ADR-0027).
- **Open slot.** The unset side of an imported line, which categorization may suggest for and the user fills (ADR-0027).
- **RuleStore.** The single atomic write path for rule files in `.finzytrack/rules/`; not a new store (ADR-0027).

## Dashboards and recipes

- **Catalog.** A self-describing registry of functions, transforms, `ledger`-step measures or visualizations that recipes may use. Docs, AI guides and a future builder are generated from it (ADR-0029).
- **Measure.** A financial quantity the `ledger` step computes (balance, flow, value, cost, unrealized or realized gain, income, budget, budget actual) by dimensions (ADR-0029).
- **Recipe.** A dashboard: a non-executable JSON document of steps and widgets that the backend interprets (INV-08, ADR-0029).
- **Step.** One node of a recipe's step graph: a `ledger` step, a `sql` step, a `bql` step, a compute function or a transform. Shared steps are declared once and reused by several widgets.
- **Widget library.** Shipped widgets that dashboards reference by id and override (ADR-0029).

## Process

- **Area review.** A fresh-context review of a whole area as it stands, at milestone ends (`design/process/PROCESS.md`).
- **Capability map.** `design/capabilities.toml`: one row per user-visible capability with status (built, partial, cut, deferred, planned), milestone, release-blocking flag, spec, ADRs, code, tests and user doc. `design/capabilities.md` is its generated index (ID, label, status, milestone). It answers "did we do X?".
- **Escape.** A defect found later than it should have been (by a later verifier, an audit, the nightly tier, the owner or a user). Each escape gets an analysis and a new or stronger gate.
- **Escalation.** A question that goes to the owner, filed in `design/inbox/`, only when no accepted ADR covers it.
- **G1 / G2.** G1 is the owner's go/no-go checkpoint after the write core (M1b); G2 is a confidence checkpoint after M4 (ADR-0046).
- **Gate / tier.** An automated check. Tiers: T0 (pre-commit, pre-push), T1 (every push), T2 (nightly), T-local (owner's machine, real-ledger copies).
- **Invariant (INV-nn).** An owner-owned rule that must hold, with at least one enforcing check (`design/INVARIANTS.md`).
- **Journal (slice journal).** `design/journal/<slice-id>.md`: the brief, decisions, verifier summary and retro of one slice. Not to be confused with the write-intent journal.
- **Lane.** The weight of process a slice gets: full lane (ledger writes), specialist lane (finance, security), light lane (read-only screens, catalogue entries) (ADR-0044).
- **Milestone (M0–M12, M1a/M1b).** The build plan's stages from foundations to release: `specs/roadmap.md` (summary and where we are), review:27 (the binding exits), ADR-0046.
- **Pending (invariant).** The only exemption a check gets: an invariant whose enforcement does not exist yet, tied to an open backlog item and a milestone.
- **Principle (EP-nn).** A "should" rule of `design/ENGINEERING.md`; deviations are allowed with a recorded reason.
- **Red team.** A standing adversarial agent with a fixed mandate (data safety, finance correctness, security, UX) whose findings become permanent tests.
- **Risk class.** High, Medium or Low, named in a slice brief; the verifier may raise it, never lower it.
- **Seeded defect.** A known bug planted on a throwaway branch to measure whether the verifiers catch it.
- **Slice.** The smallest change that delivers one verifiable behaviour, with a brief, tests first, a verifier pass and docs in the same change.
- **Spike.** A timeboxed experiment with a pass/fail verdict (for example S13, React vs Vue in WebKit).
- **Verifier.** A fresh-context agent that tries to break a slice and reports findings with IDs and repros.
