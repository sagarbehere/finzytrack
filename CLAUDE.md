# Finzytrack

Personal finance app: Beancount backend + Vue 3 frontend.

## Tech Stack
- Frontend: Vue 3 + Tailwind CSS + HeadlessUI + HeroIcons + TanStack Vue Table + ECharts
- Backend: Python + Beancount + SQLite
- See `frontend/CLAUDE.md` for frontend-specific rules (UI styles, generated API client, error handling)
- See `backend/CLAUDE.md` for backend-specific rules (error patterns, config conventions)

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
