# frontend/ (Vue 3 SPA)

Pointers only. The framework is **Vue 3** (ADR-0053, spike S13). Structure and layers: `design/ARCHITECTURE.md` §3, read with the Vue substitutions noted at its top; pins: §10 (Vue rows). UI layers and the design system: ADR-0035. Server state: ADR-0037. API contract and generated client: ADR-0038.

- Logic stays out of components: domain rules in `src/domain`, state and I/O in composables and Vue Query hooks, workflows in XState machines (INV-12). A phone UI must later be possible as new views over the same composables (ADR-0036).
- Components from `ui/` (shadcn-vue on Reka UI, vendored and unedited) and `app/` primitives; no raw `<button>/<input>/<table>`, `confirm()` or `toFixed` outside them.
- Money is the branded `Money` (decimal.js); dates are `Temporal.PlainDate` from temporal-polyfill, never a UTC "today" (INV-06, INV-07). Do not use globals that the oldest supported WKWebView lacks (spike S2).
- The API client is generated offline from `contracts/openapi.json` in the same commit as the backend change (ADR-0038); streams use fetch streams, not EventSource.
- Type gate: `vue-tsc --noEmit`. Lint: eslint-plugin-vue + eslint-plugin-vuejs-accessibility (INV-18), and the layer map in `eslint.boundaries.js` (INV-12): a new folder under `src/` or a cross-feature import is added there first, as a reviewed change. Tests: vitest, Playwright (Chromium and WebKit), model-based tests for every machine.
- Frontend build tooling goes in `frontend/scripts/` (`.mjs`).
- Gates (from `frontend/`, after `pnpm install --frozen-lockfile`): `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test` (incl. the pinned tsconfig and lint bans), `pnpm knip`, `pnpm e2e` (Playwright, Chromium and WebKit). `tests/unit/gates.test.ts` pins `tsconfig.json` whole: changing it is a deliberate edit of that test.
