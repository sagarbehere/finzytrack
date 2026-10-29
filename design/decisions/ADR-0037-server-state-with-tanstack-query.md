# ADR-0037: Server state lives in TanStack Query, scoped by workspace, invalidated by ledger version

This record decides how the frontend fetches, caches and invalidates server data.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** frontend, API

## Context

v0.3 kept server state in hand-rolled module singletons. Widgets showed results for stale parameters, and every widget ran twice on a cold start because a generator resolved before the commodities loaded (review:10 I7, I8).

## Decision

(owner, 2026-10-24)
- **TanStack Query** (`@tanstack/react-query`; `@tanstack/vue-query` if ADR-0006 keeps Vue) for all server state. Client state lives in side-effect-free module stores keyed by workspace (`useSyncExternalStore`). No Redux, Zustand or Pinia.
- **Keys are workspace-scoped and generated** (`['ws', ws, 'ledger' | 'config', …]`, `['app', …]`) from the OpenAPI scope declarations (ADR-0038); never hand-built.
- `staleTime: Infinity` for ledger-derived data, so focus refetch is a no-op. **Invalidation is driven by events:** the response `meta.ledger_version` (monotonic, so a late response cannot roll the version back), `meta.writes_ledger`, config writes, and a cheap **focus-time version check** (`GET /api/w/{ws}/version`) that catches edits made in Fava or an editor. Invalidate once per own write. The version also covers prices and config.
- **The ledger version travels in the envelope `meta`**, so no codegen change is needed for it.
- Every `queryFn` consumes `signal`, so a workspace switch aborts its queries. Large results skip structural sharing. Recipe widgets are one query per widget with a run token, so a stale response is ignored. Generators resolve only after the commodities load ("not ready" until then).
- Notification semantics: toast-class errors auto-dismiss and stay in history; modal-class errors are sticky (ADR-0039).

## Alternatives considered and rejected

- **Hand-rolled singleton stores** (legacy). Rejected: stale results and reruns.
- **Generated TanStack hooks from the API generator.** Rejected: they build their own keys, which breaks prefix invalidation by scope class (ADR-0038).
- **A frontend shared-steps query.** Dropped; the backend memo runs shared steps (ADR-0029).

## Consequences

- A switch from workspace A to B changes the keys, so nothing needs invalidating.

## Amendment (2026-10-25): a refresh that changes a row being edited

Owner: "Agreed with your recommendation on Q18." (2026-10-25, in session)
When a background refetch changes a row the user has unsaved edits on, the edit buffer moves to its `conflict` state (ARCHITECTURE.md §7): the user chooses to keep their edit or reload the new version. The user's edit does not silently win, and the refetch does not silently overwrite it. (Spike S6 modelled exactly this.)

## Amendment (2026-10-29): the frontend is Vue 3

Owner: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it." (2026-10-29, in session; S0056)
- The frontend is Vue 3 (ADR-0053), so server state lives in **TanStack Vue Query** (`@tanstack/vue-query`, ARCHITECTURE.md §3.2, §10), through hand-written query composables in `features/*/queries.ts`.
- Client state stays in side-effect-free module stores keyed by workspace, read by components through composables (ARCHITECTURE.md §3.1); `useSyncExternalStore` is React's and is not used. Pinia is still not used.
- The rejected alternative reads "generated TanStack Query helpers"; the reason is unchanged. Large results skip structural sharing and are held in `shallowRef` (ADR-0053).
- Nothing else changes: workspace-scoped generated keys, `staleTime: Infinity`, invalidation by events and the ledger version, `signal` in every `queryFn`.

## References

review:10 D8, D9, D11, I7, I8; review:23 §3.5; review:25 §3.2; ADR-0006, ADR-0018, ADR-0029, ADR-0038, ADR-0039.
