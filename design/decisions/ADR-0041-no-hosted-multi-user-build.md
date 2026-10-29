# ADR-0041: Hosted multi-user mode: don't foreclose, don't build

This record decides the stance on a future hosted, multi-user version.

- **Status:** Accepted. Amended by ADR-0063 (2026-10-27): one open workspace per user container; sharing a workspace between users excluded. Amended by ADR-0064 (2026-10-27, applied in S0047): no process-wide state; the re-check (R4) runs in hosted mode too. Note of 2026-10-29 (S0056): the desktop trust prompt is deferred too (ADR-0040).
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
- In hosted mode "one workspace at a time" holds per user (agent): each `UserContainer` has at most one open workspace, and a switch closes that user's open workspace only. Recovery before an open (ADR-0063 decision 2) covers that user's journals; different users' workspaces live under separate user roots that the hosted `LoadPolicy` confines, so one user's journal never names another user's file.
- **Removed from the deferred list:** "sharing a workspace between users". It is excluded by ADR-0063 ("No sharing anywhere"; the owner confirmed this reading, 2026-10-27: "confirming the three decisions the agent wrote in ADR-0063"); a future plan for it needs an ADR that supersedes ADR-0063.
- Multi-worker coordination stays deferred; whatever it becomes must keep each user's open workspace served by one process at a time.
- **Unchanged:** "don't foreclose, don't build", the seams and guards, INV-11's containers, the isolation matrix (a sibling workspace is registered, not open).

## References

review:18 §0–§5; review:23 §5; review:15 §2 (hosted provisioning cut); ADR-0010, ADR-0016, ADR-0017; INV-10, INV-11, INV-27, INV-28; CAP-HOSTED-WORKSPACES; ADR-0063.

## Amendment (2026-10-27): no process-wide state, and R4 in every mode (ADR-0064)

Owner, on ADR-0064: "we go back to one engine doing one job at a time and massively simplify everything" (2026-10-27, in session; the full record is ADR-0064). Applied in S0047 (convergence audit C18).
- **Superseded:** "no module-level mutable state outside an allowlist, whose only process-wide entry is the heavy-load semaphore". **Instead:** there is no process-wide state at all. ADR-0064 replaced the semaphore with one engine doing one job at a time, behind a queue that hangs off `UserContainer` (INV-11, as amended on 2026-10-27); the state registry refuses a process-wide entry (`backend/tests/architecture/test_globals.py`).
- **Superseded:** "the D2 background reconciliation is off in hosted mode". **Instead:** the re-check (R4) runs right after "saved" in every mode, hosted included, and is never switched off; it gates only the next save of that workspace (ADR-0008 and ADR-0010 amendments, ADR-0064 decision 7).
- **Unchanged:** every other seam and guard of this record.

## Note (2026-10-29): the desktop trust prompt is deferred too (ADR-0040)

Owner, 2026-10-29, in session: "I'm wondering if we should remove this feature for now. None of the beancount tools I know have this.. it'll likely cause some confusion (unless explained well) to even well-versed users.. or, at minimum, add one more friction step whose answer is difficult to give precisely or correctly. I'd say, pause this feature. Mark it as something to be done later, and be done with it. What do you think?" (S0056 verifier round 1; the orchestrator agreed).
- **Superseded in "Deferred until hosting is planned":** "(the desktop trust prompt for ledgers that run code, and its restrictive `LoadPolicy`, are NOT deferred: they ship in v0.4.0 per ADR-0010 and ADR-0040)". **Instead:** v0.4.0 has no desktop trust prompt; on desktop a ledger's plugins and `insert_pythonpath` run as Beancount runs them, and the desktop `LoadPolicy` body allows everything (ADR-0040 amendment "no trust prompt on desktop in v0.4.0"). A trust prompt is later work (BL-0156, `post-v0.4`; CAP-WS-TRUST `deferred`).
- **Unchanged:** the hosted `LoadPolicy` body and plugin allowlist stay deferred until hosting is planned; the seam is kept.
