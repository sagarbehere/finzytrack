# S0009: The Vue frontend skeleton and its CI gates (BL-0037, frontend part)

## Brief
- Goal: create `frontend/` as an empty Vue 3 shell (one heading) with the pinned toolchain and the gates every frontend slice inherits: vue-tsc with the EP-22 options, eslint (typescript-eslint strict, eslint-plugin-vue, vuejs-accessibility, the INV-06/INV-07 bans), prettier, vitest, knip, Playwright in Chromium and WebKit; a T1 job; the hooks; the licence check reading the frontend.
- Implements: BL-0037 (frontend part); ADR-0053 (Vue); ARCHITECTURE.md §3, §10 (Vue pins), §11; EP-22, EP-24 (`minimumReleaseAge`), ADR-0007 amendment (build target Safari 17).
- Capabilities: none. Invariants touched: INV-06 and INV-07 (lint bans, still pending as invariants: BL-0006, BL-0007), INV-18 (the a11y lint is on; still pending, BL-0018).
- Risk class / lane: Medium / full; tooling with no ledger or user data, so one verifier round unless a High is found (ADR-0058).
- Acceptance tests: `frontend/tests/unit/App.test.ts` (the shell renders its heading); `frontend/tests/unit/gates.test.ts` (tsconfig pinned whole; `new Date()`, `Date.now()` and `toFixed` refused, a date from given parts accepted); `frontend/e2e/smoke.spec.ts` (the built bundle loads in Chromium and WebKit); `scripts/tests/test_check_licences.py` JavaScript cases (own package left out; a name that only starts like ours kept; scoped names and licence lists).
- Expected doc impact: `frontend/CLAUDE.md` (gates); ARCHITECTURE.md §11; backlog BL-0037 note and new BL-0051; IMPROVEMENTS IMP-0016; `THIRD_PARTY_NOTICES`. No user docs.
- Open questions: none.
- Base SHA: 89a991c.

## Decisions
- `eslint.config.js`, not `.ts`: a TypeScript ESLint config needs `jiti` as an extra dependency for no gain (agent).
- `src/env.d.ts` declares `*.vue` modules for typescript-eslint, which type-checks `.ts` files without the Vue language plugin; vue-tsc resolves the real components (agent).
- Pins: newest stable on or before 2026-10-10 for the three packages ARCHITECTURE §10 does not list (`@types/node` 24.19.2, `globals` 17.13.0, `vue-eslint-parser` 10.4.1) (agent).
- The licence notices list license-checker's production tree, which includes packages the build never ships (Vue's compiler, its optional `typescript` peer): BL-0051 (agent).

## Hand-off (ADR-0057)
- Checks with their must-not-pass tests: the date and `toFixed` bans beside "a date from given parts passes"; the tsconfig pin; the licence own-package filter beside "a name that only starts like ours is kept".
- Self-mutation: 8 tried (each ban removed or widened, strict and strictTemplates off, own package kept, prefix match), 8 killed.
