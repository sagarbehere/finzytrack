# Decision records (ADRs)

This folder holds the architecture decision records of Finzytrack v0.4: why the system is the way it is.

## How to read and change them

- **One decision per file**, named `ADR-NNNN-<kebab-title>.md`. Numbers are never reused or renumbered.
- **Append-only.** A decided ADR is not edited away. A new decision **supersedes** an old one explicitly: the new ADR says "Supersedes ADR-nnnn", and the old one gets one status line "Superseded by ADR-mmmm". Typos and broken links may be fixed in place.
- **Status values:** `Proposed` (written, waiting for a named event or person), `Accepted`, `Superseded by ADR-nnnn`, `Retired`.
- **Deciders:** `owner` for decisions the owner made (marked "(owner, 2026-10-24)" in the text), `agent` for choices an agent made within the decided design. A change that hits the escalation rubric (`design/process/PROCESS.md`) needs the owner.
- **Citations:** ADRs cite invariants (`INV-nn`, `design/INVARIANTS.md`), principles (`EP-nn`, `design/ENGINEERING.md`), capability rows (`CAP-…`, `design/capabilities.md`) and the private review archive as `review:<doc> §<section>` (for example `review:D1 §13.1`). Evidence stays in that archive and is cited by ID only.
- **Code that looks surprising** carries a short `ADR-nnnn` reference; a check verifies that every referenced ADR exists.
- ADR-0001 to ADR-0050 were migrated on 2026-10-24 from the decided review documents (the consolidated lens documents, the write-path design, the rewrite decision, demo and packaging, workspaces, architecture, engineering principles, ledger metadata, and the final review's owner decisions). They record decisions as decided; they do not reopen them.

## Index

| ID | Title | Status | Date | Area |
|---|---|---|---|---|
| [ADR-0001](ADR-0001-adopt-the-development-process.md) | Adopt the development process | Accepted | 2026-10-24 | process |
| [ADR-0002](ADR-0002-greenfield-rebuild-on-next.md) | Greenfield rebuild on `next`; v0.3.0 is a tag only | Accepted | 2026-10-24 | repository, release |
| [ADR-0003](ADR-0003-workspace-invariant-numbering.md) | Workspace invariants are numbered INV-28–31 | Accepted | 2026-10-24 | process, invariants |
| [ADR-0004](ADR-0004-python-3-13-and-beancount-3-2-0.md) | Python 3.13 and Beancount 3.2.0 are pinned for v0.4.0 | Accepted | 2026-10-24 | stack |
| [ADR-0005](ADR-0005-portalocker-3-2-0-pinned.md) | portalocker 3.2.0 is pinned on purpose | Accepted | 2026-10-24 | stack, data safety |
| [ADR-0006](ADR-0006-frontend-framework-react-pending-s13.md) | Frontend framework: React, conditional on spike S13 | Superseded by ADR-0053 | 2026-10-24 | frontend, stack |
| [ADR-0007](ADR-0007-tauri-2-desktop-shell.md) | Tauri 2 desktop shell with a Python sidecar | Accepted | 2026-10-24 | desktop, release |
| [ADR-0008](ADR-0008-write-safety-contract.md) | Every ledger write passes the write-safety contract | Accepted | 2026-10-24 | write path, data safety |
| [ADR-0009](ADR-0009-fava-style-source-splicing.md) | Fava-style source splicing; mutators are edit producers | Accepted | 2026-10-24 | write path |
| [ADR-0010](ADR-0010-overlay-load-with-load-policy.md) | Validate candidate text with an overlay load (D2), behind a LoadPolicy | Accepted | 2026-10-24 | write path, ledger |
| [ADR-0011](ADR-0011-no-resident-ledger.md) | No resident ledger; bounded memory and serialized writes | Accepted | 2026-10-24 | backend, performance, memory |
| [ADR-0012](ADR-0012-write-intent-journal-and-recovery.md) | Write-intent journal and one recovery spec | Accepted | 2026-10-24 | write path, data safety, lifecycle |
| [ADR-0013](ADR-0013-atomic-swap-replace.md) | Replace by atomic swap; the external-editor race is accepted with detection | Accepted | 2026-10-24 | write path, data safety |
| [ADR-0014](ADR-0014-mutator-promotion-and-clearance.md) | Mutator promotion, clearance by kind of edit, and the old app as oracle | Accepted | 2026-10-24 | write path, process |
| [ADR-0015](ADR-0015-mirror-rebuildable-cache-atomic-publish.md) | The mirror is a rebuildable cache, published atomically | Accepted | 2026-10-24 | mirror, data safety |
| [ADR-0016](ADR-0016-sqlite-only-query-engine.md) | SQLite is the only query engine, behind one hardened QueryRunner | Accepted | 2026-10-24 | mirror, recipes, security |
| [ADR-0017](ADR-0017-the-folder-is-the-workspace.md) | The folder is the workspace; state placement | Accepted | 2026-10-24 | workspaces, state placement |
| [ADR-0018](ADR-0018-workspace-addressed-api.md) | The API is workspace-addressed; there is no server-side active workspace | Accepted | 2026-10-24 | API, workspaces |
| [ADR-0019](ADR-0019-workspace-and-app-lifecycle.md) | App and workspace lifecycle: assess, plan, never write newer | Accepted | 2026-10-24 | lifecycle, migrations |
| [ADR-0020](ADR-0020-legacy-adoption-copy-by-default.md) | Legacy adoption: Copy by default, Share gated; the legacy install keeps working | Accepted | 2026-10-24 | workspaces, lifecycle, data safety |
| [ADR-0021](ADR-0021-workspace-policy-ai-secrets-sharing.md) | Workspace policy: AI on by default, scoped secrets, copy-based sharing | Accepted | 2026-10-24 | workspaces, AI, secrets |
| [ADR-0022](ADR-0022-demo-workspace.md) | The demo is a generated, disposable workspace, replaced on release | Accepted | 2026-10-24 | content, lifecycle |
| [ADR-0023](ADR-0023-money-dates-and-number-formatting.md) | Money, dates and number formatting | Accepted | 2026-10-24 | domain kernel |
| [ADR-0024](ADR-0024-beancount-oracle-and-ledger-profile.md) | Beancount is the oracle; one Ledger Profile serves ledger facts | Accepted | 2026-10-24 | domain kernel, ledger |
| [ADR-0025](ADR-0025-finance-semantics-defaults.md) | Finance semantics defaults | Accepted | 2026-10-24 | domain kernel, finance |
| [ADR-0026](ADR-0026-ledger-metadata-contract.md) | The ledger metadata contract; one import identity key; optional cleanup | Accepted | 2026-10-24 | ledger format, import |
| [ADR-0027](ADR-0027-import-pipeline-and-categorization.md) | Import pipeline on the backend, anchored on postings; tiered categorization | Accepted | 2026-10-24 | import |
| [ADR-0028](ADR-0028-statement-reconciliation.md) | Statement reconciliation is a first-class use of the matcher | Accepted | 2026-10-24 | import, reconciliation |
| [ADR-0029](ADR-0029-dashboards-as-recipes.md) | Dashboards are user-authored recipes, run on the backend; budgets as measures | Accepted | 2026-10-24 | recipes, dashboards, budgets |
| [ADR-0030](ADR-0030-investment-scope-reset-and-prices.md) | Investment scope reset; prices | Accepted | 2026-10-24 | investments, prices |
| [ADR-0031](ADR-0031-every-beancount-booking-method.md) | Every booking method Beancount books is supported | Accepted | 2026-10-24 | investments, ledger |
| [ADR-0032](ADR-0032-broker-sanity-check-and-one-upload-sync.md) | Broker sanity check, holdings reconciliation and one-upload sync | Accepted | 2026-10-24 | investments, import |
| [ADR-0033](ADR-0033-ai-assistant-capabilities-and-proposals.md) | One AI assistant over shared capabilities; changes are proposals, never auto-applied | Accepted | 2026-10-24 | AI |
| [ADR-0034](ADR-0034-ai-stack.md) | AI stack: Pydantic AI, AI SDK v7, markdown-it + DOMPurify, our own components | Accepted | 2026-10-24 | AI, stack |
| [ADR-0035](ADR-0035-ui-layers-and-design-system.md) | UI layers and design system: shadcn/ui on Radix plus our own domain primitives | Accepted | 2026-10-24 | frontend, UI, accessibility |
| [ADR-0036](ADR-0036-frontend-rewrite-structure-and-phone-principle.md) | Frontend rewrite from a behaviour spec; structure; desktop first, phone not precluded | Accepted | 2026-10-24 | frontend |
| [ADR-0037](ADR-0037-server-state-with-tanstack-query.md) | Server state lives in TanStack Query, scoped by workspace, invalidated by ledger version | Accepted | 2026-10-24 | frontend, API |
| [ADR-0038](ADR-0038-api-contract-and-code-generation.md) | API contract: one envelope, plans and confirm, offline OpenAPI, openapi-typescript | Accepted | 2026-10-24 | API, codegen |
| [ADR-0039](ADR-0039-errors-logging-and-diagnostics.md) | Errors, logging and diagnostics | Accepted | 2026-10-24 | backend, frontend, observability, privacy |
| [ADR-0040](ADR-0040-security-foundations.md) | Security foundations are part of the shell from the first build | Accepted | 2026-10-24 | security, privacy |
| [ADR-0041](ADR-0041-no-hosted-multi-user-build.md) | Hosted multi-user mode: don't foreclose, don't build | Accepted | 2026-10-24 | architecture, hosting |
| [ADR-0042](ADR-0042-licence.md) | Licence: owner code GPL-2.0-or-later; notices and licence checks; legal advice before v0.4.0 | Accepted | 2026-10-24 | legal, release |
| [ADR-0043](ADR-0043-release-scope-and-release-gate.md) | Release scope: exactly four capabilities are deferred; the release gate | Accepted | 2026-10-24 | release, scope |
| [ADR-0044](ADR-0044-process-lanes-and-review-cadences.md) | Process lanes and review cadences | Accepted | 2026-10-24 | process |
| [ADR-0045](ADR-0045-local-agents-deterministic-ci-direct-commits.md) | Agents run locally; CI runs deterministic gates only; direct commits on `next` | Accepted | 2026-10-24 | process, CI, repository |
| [ADR-0046](ADR-0046-g1-checkpoint-and-pilot.md) | The G1 go/no-go checkpoint, the pilot, and the M1a/M1b split | Accepted | 2026-10-24 | process, planning |
| [ADR-0047](ADR-0047-product-defaults.md) | Product defaults for v0.4.0 | Accepted | 2026-10-24 | product |
| [ADR-0048](ADR-0048-v0-2-2-names-kept-through-0-4-x.md) | v0.2.2 mirror tables and compute functions keep their names through 0.4.x | Accepted; the `lots` part awaits the owner's re-confirmation | 2026-10-24 | mirror, recipes, compatibility |
| [ADR-0049](ADR-0049-account-move-merge-delete.md) | Account rename, merge and delete are built on one move-postings primitive | Accepted | 2026-10-24 | accounts, write path |
| [ADR-0050](ADR-0050-agent-level-architecture-and-toolchain-choices.md) | Agent-level architecture and toolchain choices | Accepted | 2026-10-24 | architecture, stack, verification |
| [ADR-0051](ADR-0051-working-principles-from-the-review.md) | Working principles carried over from the review | Accepted | 2026-10-24 | process, engineering |
| [ADR-0052](ADR-0052-default-currency-lives-in-the-ledger.md) | The default currency lives in the ledger; the tool setting is only an override | Accepted | 2026-10-24 | workspaces, settings, ledger |
| [ADR-0053](ADR-0053-frontend-framework-is-vue.md) | The frontend framework is Vue 3 (spike S13 outcome) | Accepted | 2026-10-25 | frontend, stack |
| [ADR-0054](ADR-0054-beancount-in-its-own-engine-process.md) | Beancount runs in its own ledger-engine process; the main sidecar never imports it | Accepted | 2026-10-25 | architecture, licence, backend |
| [ADR-0055](ADR-0055-mirror-legacy-postings-columns.md) | The mirror keeps the documented v0.2.2 `postings` columns, computed by the mirror | Accepted | 2026-10-25 | mirror, compatibility |
| [ADR-0056](ADR-0056-python-build-standalone-and-linux-packages.md) | Sidecar packaging uses python-build-standalone; Linux ships AppImage and .deb | Accepted | 2026-10-25 | packaging, desktop |
| [ADR-0057](ADR-0057-pilot-result.md) | The pilot's result: hand-off checks, re-verification, and a first slice count | Accepted | 2026-10-25 | process |
| [ADR-0058](ADR-0058-process-overhead-trim.md) | Verification scaled to risk, process checks built on evidence, shorter journals | Accepted | 2026-10-25 | process |
| [ADR-0059](ADR-0059-encrypted-ledgers-not-read-in-v0-4-0.md) | Encrypted ledger files are not read in v0.4.0 | Accepted | 2026-10-25 | ledger loading, scope |
| [ADR-0060](ADR-0060-github-ci-runs-manually-before-releases.md) | GitHub CI runs manually, before each release; the local hooks are the per-push gates | Accepted | 2026-10-25 | process, CI |
| [ADR-0061](ADR-0061-macos-first-cross-platform-later.md) | macOS first; Linux and Windows in dedicated sessions after v0.4.0 works on macOS, except the write path's Windows semantics | Accepted | 2026-10-26 | process, platforms, release |

## Template

Copy this into a new file `ADR-NNNN-<kebab-title>.md` (next free number). Keep it short, typically 20–60 lines.

```markdown
# ADR-NNNN: <Decision as a short statement>

<One sentence: what this record decides.>

- **Status:** Proposed | Accepted | Superseded by ADR-nnnn
- **Date:** YYYY-MM-DD
- **Deciders:** owner | agent
- **Area:** <for example write path, import, frontend, process>

## Context

<The forces and facts that make a decision necessary. Cite evidence by ID; never paste raw findings, real ledger data or secrets.>

## Decision

<What is decided, stated plainly. Mark owner decisions "(owner, YYYY-MM-DD)" and agent choices "(agent)".>

## Alternatives considered and rejected

- **<Alternative>.** Rejected: <why>.

## Consequences

<What becomes easier or harder; follow-up work; what tests or checks enforce it.>

## References

<review:… IDs, INV-nn, EP-nn, CAP-…, other ADR-nnnn, spec anchors.>
```
