# ADR-0018: The API is workspace-addressed; there is no server-side active workspace

This record decides how requests name the workspace they act on.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** API, workspaces

## Context

v0.3 keeps one server-side "current ledger". Switching it in one browser tab silently retargets the next write of another tab, and a failed switch was persisted (review:10 I6). The same shared pointer broke hosted tenancy (review:15 L10-01).

## Decision

(owner, 2026-10-24)
- **Every workspace-scoped endpoint lives under `/api/w/{ws}/…`**, where `{ws}` is the registry key. App-scoped endpoints (`/api/boot`, `/api/app/…`, `/api/workspaces/…`) do not.
- `{ws}` resolves only in the caller's registry to a `WorkspaceContainer`. Unknown → 404 `WORKSPACE_UNKNOWN`; not ready → 409 `WORKSPACE_NOT_READY {phase}`.
- **On the frontend the key comes from the route** (`/w/:ws/…`) and flows into the query key and the API call together. The generated client takes `ws` as an explicit path parameter.
- **Switching** is the frontend moving from `/w/A/…` to `/w/B/…` after the backend has opened and validated B. The registry records an open only after `READY`/`WARMING` (INV-29). A write started in A always completes in A.
- The response envelope's `meta` carries `{workspace_id, ledger_version}`. A response for another workspace is a client-asserted bug.
- Several native windows on different workspaces are architecturally allowed; the UI is deferred (CAP-WS-MULTIWINDOW). Browser tabs already work.

## Alternatives considered and rejected

- **A server-side active workspace** (legacy). Rejected: a shared mutable pointer.
- **A request-header interceptor fed by a global "current workspace" value.** Rejected: a mutation built in A and sent just after a switch would go to B.

## Consequences

- A route-scope test checks that every route declares `app` or `workspace` scope (`x-ft-scope`).
- Query keys are workspace-scoped and generated (ADR-0037, ADR-0038).

## References

review:23 §3.2–§3.10; review:10 I6; review:15 L10-01; ADR-0037, ADR-0038; INV-28, INV-29; CAP-WS-SWITCH, CAP-WS-MULTIWINDOW.
