# ADR-0036: Frontend rewrite from a behaviour spec; structure; desktop first, phone not precluded

This record decides how the frontend is rebuilt, how it is structured, and the phone UI principle.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** frontend

## Context

The large v0.3 components mixed four layers: data shaping that belongs on the backend, server-state plumbing, UI-kit re-implementation, and real behaviour. TransactionTable owned its parents' data through an expose/echo protocol. Re-running a query discarded unsaved edits; edits typed during a save were never sent; undo could bring back an undone change (review:19 L8-01, L8-02, M-1). Incremental decomposition would edit about 90% of lines anyway.

## Decision

(owner, 2026-10-24)
- **Rewrite the frontend** against the behaviour spec (the review's per-component specs with KEEP/DROP/REDESIGN verdicts plus the verifier's additions), which moves into `design/specs/`. The spec is behaviour-level and framework-neutral.
- **Structure:** `src/domain` holds pure rules; `features/*` hold headless hooks (`useEditBuffer` with per-op inverse patches, `useImportSession`, `useBudgetDrafts`, …), XState machines for workflows with real states, and thin views; `components/app` and `components/ui` per ADR-0035. Parents own data; the grid emits intents.
- **No business rule lives only in a view's JSX** (or template) (INV-12).
- **Transaction behaviour:** delete is immediate with a confirm (Cancel focused) plus undo; the disabled "Balance" column is dropped (running balances live in a per-account statement view with a backend endpoint); column layouts are per surface; the grid follows the WAI-ARIA grid pattern (navigation and edit modes; Alt+↑/↓ always navigates).
- **Import drafts survive a restart** by spilling each session to machine-local storage (ADR-0027).
- **Build order:** shell/boot first (on the boot phases), then Accounts, **Transactions early**, Import, Dashboards, Investments, Assistant, Settings.
- **The acceptance suite comes first:** the review's 16 Playwright flows plus about 20 the verifier added, on an isolated backend with its own port and data dir, a fresh demo per run, a frozen clock on both sides, an LLM stub and `TZ` west of UTC.
- **Phone principle (owner):** polish the desktop app first; do not build a half-baked phone UI. Offer a capability on the phone later only if its phone UX can be excellent. Logic stays outside components (domain, use cases, hooks), the API serves capabilities (paginated, filterable), primitives stay responsive-capable, and the shell does not hard-wire the desktop sidebar into feature views. **The existing card views are deleted**; tables scroll horizontally on narrow screens.
- Storage keys follow a versioned scheme (`ft/v1/app|ws/…`), scoped per workspace.

## Alternatives considered and rejected

- **Incremental decomposition** of the old components. Rejected: about 90% of lines edited anyway, and the intermediate state paid for twice.
- **A per-route cut-over inside the old app.** Superseded by the greenfield (ADR-0002); write equivalence is checked by semantic goldens and the oracle, not byte identity.
- **Keeping separate phone card views.** Rejected: duplicated editing code and a half-built phone UI.

## Consequences

- About 2,800–3,000 lines of unit tests port directly.
- A phone UI (CAP-PHONE-UI) stays possible as a new set of views over the same hooks.

## References

review:19 §0–§4; review:17 §5; review:L8 §1 (behaviour spec, private archive); ADR-0002, ADR-0006, ADR-0027, ADR-0035, ADR-0037; INV-12, INV-16; CAP-PHONE-UI, CAP-TXN-CARDS.
