# ADR-0041: Hosted multi-user mode: don't foreclose, don't build

This record decides the stance on a future hosted, multi-user version.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** architecture, hosting

## Context

A hosted service on a cheap, memory-limited server may come later. The per-user container structure is sound, and the decided designs foreclose nothing if a few conditions hold. In hosted mode, however, everything a tenant authors is untrusted: settings fields, ledger text (includes, plugins, documents roots), recipe SQL and BQL, and `${VAR}` references.

## Decision

(owner, 2026-10-24) **Do not build hosted mode for v0.4.0, and do not foreclose it.** Keep these seams and guards now:
- every service hangs off `UserContainer` or `WorkspaceContainer` (INV-11); no module-level mutable state outside an allowlist, whose only process-wide entry is the heavy-load semaphore;
- everything a tenant authors is resolved through policy objects, permissive on desktop and confined in hosted mode: the **`LoadPolicy`** on every load path (ADR-0010), the path guard on every path-like config field, query confinement (ADR-0016), and an **injected per-user secret resolver** (desktop behaviour unchanged);
- every cache is per user, bounded by a configurable limit, evictable and optional in hosted mode (INV-10);
- content packs stay read-only; mirror generations live under each user root;
- the D2 background reconciliation is off in hosted mode;
- the workspace format is shared with hosted, and `assess_portability(ws, 'hosted')` is the hosted import validator (ADR-0017);
- an **effect-based isolation matrix** (tenant × workspace) asserts that attacks change nothing for the other tenant or a sibling workspace.

**Deferred until hosting is planned** (CAP-HOSTED-WORKSPACES): quotas, multi-worker coordination, proxy authentication and the trust model, provisioning, billing and an admin UI, the hosted `LoadPolicy` body and plugin allowlist (the desktop trust prompt for ledgers that run code, and its restrictive `LoadPolicy`, are NOT deferred: they ship in v0.4.0 per ADR-0010 and ADR-0040), the per-user secret store, SSRF controls, eviction refcounting, whether hosted tenants get BQL, sharing a workspace between users, hosted legacy adoption, and the hosted consent flow for lifecycle runs (review:15 Q5).

## Alternatives considered and rejected

- **Build hosted mode now.** Rejected: no hosted deployment is planned, and it would slow the desktop release.
- **Ignore hosting entirely.** Rejected: a few cheap seams now avoid a redesign later.

## Consequences

- Desktop bugs found by the multi-user lens (namespacing of UI memory by workspace, IMAP certificate checks, the orphan scan) are fixed regardless of hosting.

## Amendment (2026-10-27): one open workspace per user container (ADR-0063)

Owner: "1. Yes to forbidding two workspaces sharing a file. If it'll help we can simplify further: No sharing anywhere, no multiple instances of Finzytrack anywhere. Period. Finzytrack is just about one instance, working with one workspace at a time." (2026-10-27, in session; the full quote is in ADR-0063). Details marked "(agent)" are ADR-0063's.
- In hosted mode "one workspace at a time" holds per user (agent): each `UserContainer` has at most one open workspace, and a switch closes that user's open workspace only. The overlap check runs within one user's registry; different users' workspaces live under separate user roots that the hosted `LoadPolicy` confines.
- **Removed from the deferred list:** "sharing a workspace between users". It is excluded by ADR-0063 ("No sharing anywhere"); a future plan for it needs an ADR that supersedes ADR-0063.
- Multi-worker coordination stays deferred; whatever it becomes must keep each user's open workspace served by one process at a time.
- **Unchanged:** "don't foreclose, don't build", the seams and guards, INV-11's containers, the isolation matrix (a sibling workspace is registered, not open).

## References

review:18 §0–§5; review:23 §5; review:15 §2 (hosted provisioning cut); ADR-0010, ADR-0016, ADR-0017; INV-10, INV-11, INV-27, INV-28; CAP-HOSTED-WORKSPACES; ADR-0063.
