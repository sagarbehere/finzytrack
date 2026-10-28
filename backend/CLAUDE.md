# backend/ (Python sidecar)

Pointers only. Package map, dependency rules and pins: `design/ARCHITECTURE.md` §2 and §10. Principles: `design/ENGINEERING.md`. Write safety: the D1 contract ADRs in `design/decisions/` and INV-01, INV-22.

- Python and Beancount versions are pinned on purpose (ADR-0004); lock with `uv`, never install outside the lockfile.
- This is the main sidecar (GPLv3): it **never imports Beancount**; Beancount lives in the separate ledger engine (`engine/`, ADR-0054; `.importlinter`, `tests/architecture/` and `scripts/check_engine_boundary.py` enforce it). Ledger work goes through `app.platform.engine.EnginePort` only; never import `ledger_engine`. No module-level mutable state outside the allowlist (INV-11).
- The engine tests here (`tests/platform/test_engine_client.py`) spawn the real engine from `engine/.venv`: run `uv sync --locked` in `engine/` too.
- Gates: `uv sync --locked`, then from `backend/`: `ruff format --check`, `ruff check`, `pyright` (strict), `lint-imports`, `deptry src`, `pytest`.
- Money is `Decimal` and dates are civil dates (INV-06, INV-07). Errors use the one taxonomy (ARCHITECTURE.md §6).
- The OpenAPI spec is exported offline and the TypeScript client regenerated in the same commit (ARCHITECTURE.md §4.1); there is no "restart the backend and regenerate" step. From the repo root: `backend/.venv/bin/python -I backend/scripts/export_openapi.py`, then `node frontend/scripts/gen-api-types.mjs`; `scripts/check_generated.py` refuses a commit without both.
- Backend-internal scripts go in `backend/scripts/` and run with cwd = `backend/`.
- The mirror's read half (`src/app/mirror/`, S0052): the app's own reads go through `reader.Reader`, every user query through `query.QueryRunner` (ADR-0016), never a connection of its own. Measure on a built mirror with `scripts/measure_mirror_reads.py`.
- A test written before its capability exists carries `@pytest.mark.fixme(id=…, due=…, reason=…)` (a strict xfail that may fail only with `PlannedMissing`, S0032) and reaches planned modules through `tests/integrity/planned.py`, so once the module exists any failure is red; `id` is an open backlog row due by `due` (`scripts/check_invariants.py`, S0029). The landing slice removes the marker, imports normally and closes the row.
