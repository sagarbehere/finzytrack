# backend/ (Python sidecar)

Pointers only. Package map, dependency rules and pins: `design/ARCHITECTURE.md` §2 and §10. Principles: `design/ENGINEERING.md`. Write safety: the D1 contract ADRs in `design/decisions/` and INV-01, INV-22.

- Python and Beancount versions are pinned on purpose (ADR-0004); lock with `uv`, never install outside the lockfile.
- Only `ledger.beancount_port` imports Beancount (INV-02). No module-level mutable state outside the allowlist (INV-11).
- Money is `Decimal` and dates are civil dates (INV-06, INV-07). Errors use the one taxonomy (ARCHITECTURE.md §6).
- The OpenAPI spec is exported offline and the TypeScript client regenerated in the same commit (ARCHITECTURE.md §4.1); there is no "restart the backend and regenerate" step.
- Backend-internal scripts go in `backend/scripts/` and run with cwd = `backend/`.
