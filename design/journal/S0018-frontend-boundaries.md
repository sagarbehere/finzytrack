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

## Verifier
- Round 1, head 7b8d369: FAIL. Attacks: 14 (every import form; Vue SFCs; specifiers TypeScript resolves but the plugin does not; other script extensions; nested look-alike folders; @tauri-apps; packages in domain; feature boundaries; fixture fidelity against the real config; 21 mutants; the root config; dynamic and glob imports; the 14-day rule, held for all 24 new lock entries; tests on base).
- F1 High: `'../platform/clock.js'` (the usual TypeScript spelling for clock.ts) was not resolved by the plugin, so the rule skipped it, while typecheck and build passed. Fixed: `checkUnknownLocals: true` (an unplaceable local import is refused); tests for `.js`-for-`.ts` and a missing file.
- F2 Medium: only `.ts`/`.vue` files were checked; an `.mts` in domain or a stray `.js` passed. Fixed: every script kind under `src/`; tests.
- F3 Medium (tests): app deep-importing a feature and 11 other forbidden pairs, plus feature → components/app, had no case. Added; the verifier's 21 mutants now leave one survivor, equivalent (`core` origin for decimal.js/temporal-polyfill, which are not core modules).
- F4 Low: alias-looking specifiers (`@/`, `src/`) counted as packages. Fixed: refused as packages; tests.
- F5 Low: `mode: 'full'` is deprecated. Kept: `partialMatch: false` did not classify `src/main.ts` the same way when tried (Decisions above); revisit in the plugin's upgrade slice.
- F6 Low: `frontend/CLAUDE.md` now names the layer map. F7 Low: INV-12's line states that `import(variable)` and `import.meta.glob(...)` are review-only.
- Re-verification required (High fix): round 2 below.
- Round 2, head bcd2949: PASS WITH FINDINGS. Attacks: 6 (round-1 repros; bypass probes incl. `?raw`, absolute and `#` specifiers, `/@fs/`, targets outside src, dynamic imports, other extensions; false refusals of legitimate same-layer imports; the real tree; 16 mutants; INV-12's wording).
  - F1 Medium: `checkUnknownLocals` refuses Vite query imports (`?url`, `?raw`, `?worker`) and the `./x.js`-for-`x.ts` spelling, which compile and build. Resolution: both refusals are deliberate for now (fail closed), stated in `eslint.boundaries.js` and `frontend/CLAUDE.md`, pinned by tests; resolving query imports is BL-0056, for the first slice that imports an asset.
  - F2 Low: refusal rows for `.tsx`, `.cts`, `.jsx`, `.mjs`, `.cjs` and the `~/` alias added. F3 Low: the dead `src/**` pattern removed (the bare `src` is what a `src/…` specifier is matched as).
  - No High: no further round (ADR-0058).

## Retro
- The first map was built from the examples in §3.1, not from the ways an import can be written; the verifier's list of import forms (extensions, aliases, unresolvable spellings) was the spec to start from — the same lesson as S0010's CSP guard.
- Committing with `;` instead of `&&` let a failing invariant check through (7b8d369 fixed it); chain gates with `&&`.
- Process cost: about 1 h 15 min building (much of it learning the plugin's v7 config); verifiers 7 + 3 min; fixes about 40 min.
