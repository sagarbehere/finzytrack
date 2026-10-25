# backend/ (Python sidecar)

Pointers only. Package map, dependency rules and pins: `design/ARCHITECTURE.md` §2 and §10. Principles: `design/ENGINEERING.md`. Write safety: the D1 contract ADRs in `design/decisions/` and INV-01, INV-22.

- Python and Beancount versions are pinned on purpose (ADR-0004); lock with `uv`, never install outside the lockfile.
- This is the main sidecar (GPLv3): it **never imports Beancount**; Beancount lives in the separate ledger engine (ADR-0054; `.importlinter` and `tests/architecture/` enforce it). No module-level mutable state outside the allowlist (INV-11).
- Gates: `uv sync --locked`, then from `backend/`: `ruff format --check`, `ruff check`, `pyright` (strict), `lint-imports`, `deptry src`, `pytest`.
- Money is `Decimal` and dates are civil dates (INV-06, INV-07). Errors use the one taxonomy (ARCHITECTURE.md §6).
- The OpenAPI spec is exported offline and the TypeScript client regenerated in the same commit (ARCHITECTURE.md §4.1); there is no "restart the backend and regenerate" step.
- Backend-internal scripts go in `backend/scripts/` and run with cwd = `backend/`.
- A test written before its capability exists carries `@pytest.mark.fixme(id=…, due=…, reason=…)` (a strict xfail) and reaches planned modules through `tests/integrity/planned.py`; `id` is an open backlog row due by `due` (`scripts/check_invariants.py`, S0029). The landing slice removes the marker, imports normally and closes the row.
