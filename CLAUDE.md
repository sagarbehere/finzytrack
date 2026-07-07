# Finzytrack

Personal finance app: Beancount backend + Vue 3 frontend.

## Tech Stack
- Frontend: Vue 3 + Tailwind CSS + HeadlessUI + HeroIcons + TanStack Vue Table + ECharts
- Backend: Python + Beancount + SQLite
- See `frontend/CLAUDE.md` for frontend-specific rules (UI styles, generated API client, error handling)
- See `backend/CLAUDE.md` for backend-specific rules (error patterns, config conventions)

## Repository layout — where scripts live

There are three `scripts/` directories, split by scope. Put a script in the one matching its reach; don't create a fourth or move a script across the boundary:

- **`backend/scripts/`** — backend-internal Python dev/debug tools (e.g. ledger duplicate-finder, fake-ledger generator). Assume **cwd = `backend/`** and import `app.*` directly.
- **`frontend/scripts/`** — frontend build tooling (Node `.mjs`), invoked via npm `predev`/`prebuild` hooks and `desktop/build.py`.
- **`scripts/` (repo root)** — cross-cutting tools that span both packages or operate on repo-wide artifacts (e.g. `sync_ai_reference.py` syncs frontend→backend, `migrate_recipes.py`, schema-doc generation). Run from the repo root; they anchor to `ROOT = Path(__file__).resolve().parents[1]` and reach into `backend/…`/`frontend/…` by explicit path.

## Cross-Cutting Rules

### Money handling — single source of truth
Any value that represents an amount of a commodity (currency, share, unit) follows the contract in [`dev-docs/money-types.md`](dev-docs/money-types.md). Short version: `Decimal` in Python, `TEXT` in SQLite, JSON string on the wire, branded `Money` (decimal.js-backed) in TypeScript. Float appears only at the display formatter and at explicit `SUM(CAST(amount AS REAL))` aggregations. See [`dev-docs/refactoring-money-types.md`](dev-docs/refactoring-money-types.md) for the per-file audit.

### Backend-then-frontend workflow
When a task involves both backend and frontend changes:
1. Complete all backend work first (endpoints, models, error codes)
2. **Stop and ask the user** to restart the backend and regenerate the frontend API. Do not attempt to regenerate yourself.
3. After the user confirms, verify generated types, then proceed with frontend work.
4. Run `npx vue-tsc --noEmit` from `frontend/` to verify TypeScript after all changes.

### Type checking
- Frontend: `npx vue-tsc --noEmit` from `frontend/`
- API codegen: `npm run generate-api` from `frontend/` (non-streaming endpoints only)

### Recipe schema — one source of truth, generated everywhere else
`frontend/src/types/recipe.schema.json` is the single source of truth for the recipe format. Its TS types, runtime enum consts (`recipes.enums.generated.ts`), the Python validator's enum sets, the byte-identical backend schema copy, and the AI prose-doc appendix are all **generated/derived** from it — never hand-edit those. In dev, the backend regenerates the backend-side copies at startup (`autosync_dev`); the frontend regenerates its TS/enums via the `predev`/`prebuild` hooks.

**Install the pre-commit hook once per clone** so a schema edit can't be committed with stale generated artifacts:
```
git config core.hooksPath .githooks
```
The hook (`.githooks/pre-commit`) is a no-op unless the schema or a generator is staged, then it regenerates and blocks the commit if `recipes.generated.ts`, `recipes.enums.generated.ts`, or `schema_recipe_dashboard.md` would change.
