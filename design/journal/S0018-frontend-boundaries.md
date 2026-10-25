# S0018: The frontend's layer map as a lint (BL-0012, frontend half)

## Brief
- Goal: enforce ARCHITECTURE.md §3.1's dependency direction in `frontend/src` (Vue reading): app → features → (catalog, components/app, api, platform) → domain; components/ui imports nothing of ours; api/generated only from api; a feature reached only through its `index.ts`, by app or along a listed cross-feature edge; domain imports only domain, decimal.js and temporal-polyfill; `@tauri-apps/*` only in `platform/shell/tauri.ts` (EP-07); every file under `src/` belongs to an element. Makes INV-12 `enforced` with the backend half from S0006.
- Implements: INV-12, EP-07, EP-11, ARCHITECTURE.md §3.1 and §10 (eslint-plugin-boundaries 7.2.0); BL-0012.
- Capabilities: none. Invariants touched: INV-12 (pending → enforced).
- Risk class / lane: Medium / full (an architecture gate; no ledger or user data).
- How it could be loosened or bypassed (written before coding): 1. an import the map does not mention passes (default allow) → `default: 'disallow'`; 2. packages are not checked at all (the rule's default is local only) → `checkAllOrigins: true` with explicit package policies; 3. a new folder outside the map is unclassified and so unchecked → `no-unknown-files`; 4. a feature deep-importing another feature's files; 5. a feature reaching any feature (captured name not compared); 6. app deep-importing feature internals; 7. `@tauri-apps` from anywhere; 8. domain importing a package or a Node core module; 9. lower layers importing higher ones (each edge); 10. a test that passes because imports do not resolve (fixture tree with stub packages, so every import resolves). Stated limit: the second sentence of INV-12 (logic in templates) is review-only.
- Acceptance tests: `frontend/tests/unit/boundaries.test.ts` (16 refusals, 6 allowances, the edge mechanism both ways).
- Expected doc impact: INVARIANTS.md INV-12; backlog BL-0012; roadmap §1; this journal.
- Open questions: none.
- Base SHA: a111bb6.

## Decisions
- The map lives in `eslint.boundaries.js` (with a `.d.ts` for the strict tsconfig), used by `eslint.config.js` and by the test on a scratch tree outside the repo (agent).
- `mode: 'full'` is deprecated in 7.x; its suggested replacement `partialMatch: false` did not classify `src/main.ts` the same way when tried, so the pinned behaviour stays, and the plugin's console warning with it, until the next upgrade slice (agent).
- `knip` reads `eslint.boundaries.js` as an entry (otherwise it reports the plugin as unused) (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: every loosening above has a refusal case; allowances guard against a map that refuses everything.
- Self-mutation (scratch copy with node_modules linked, since the runner cannot link them): 9 tried, 9 killed.
