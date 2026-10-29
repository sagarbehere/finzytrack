// INV-12 for the frontend (BL-0012): the dependency direction of ARCHITECTURE.md §3.1.
// app -> features -> (catalog, components/app, api, platform) -> domain;
// components/ui imports nothing of ours; api/generated is read only by api; app and other features reach a
// feature only through its index.ts, and a feature reaches another only along an edge in FEATURE_EDGES;
// src/domain imports only domain, decimal.js and temporal-polyfill; only platform/shell/tauri.ts imports
// @tauri-apps (EP-07). Every file under src/ must belong to an element (no-unknown-files), so a new folder
// fails until it is placed in this map. Tests: tests/unit/boundaries.test.ts. Out of reach of a static lint
// (stated in INV-12): `import(variable)` and `import.meta.glob(...)`.
import boundaries from 'eslint-plugin-boundaries'

// Cross-feature edges, [from, to] by feature folder name. None exist in M0; each one is a reviewed decision.
export const FEATURE_EDGES = []

const elements = [
  { type: 'app', pattern: 'src/app' },
  { type: 'app', pattern: ['src/main.ts', 'src/App.vue', 'src/env.d.ts'], mode: 'full' },
  { type: 'feature', pattern: 'src/features/*', capture: ['feature'] },
  { type: 'catalog', pattern: 'src/catalog' },
  { type: 'components-app', pattern: 'src/components/app' },
  { type: 'components-ui', pattern: 'src/components/ui' },
  { type: 'api-generated', pattern: 'src/api/generated' },
  { type: 'api', pattern: 'src/api' },
  { type: 'shell-tauri', pattern: 'src/platform/shell/tauri.ts', mode: 'full' },
  { type: 'platform', pattern: 'src/platform' },
  { type: 'domain', pattern: 'src/domain' },
]

const allow = (from, to) => ({
  from: { element: { type: from } },
  allow: { to: { element: { types: { anyOf: to } } } },
})
const featureIndex = (name) => ({
  element: { type: 'feature', captured: { feature: name } },
  file: { path: `**/features/${name}/index.ts` },
})

export function policies(edges = FEATURE_EDGES) {
  return [
    allow('app', ['app', 'catalog', 'components-app', 'components-ui', 'api', 'platform', 'domain']),
    { from: { element: { type: 'app' } }, allow: { to: featureIndex('*') } },
    allow('catalog', ['catalog', 'api', 'platform', 'domain']),
    allow('components-app', ['components-app', 'components-ui', 'catalog', 'domain']),
    allow('components-ui', ['components-ui']),
    allow('api', ['api', 'api-generated', 'platform', 'domain']),
    allow('api-generated', ['api-generated']),
    allow('platform', ['platform', 'domain']),
    allow('shell-tauri', ['platform', 'domain']),
    allow('domain', ['domain']),
    allow('feature', ['catalog', 'components-app', 'components-ui', 'api', 'platform', 'domain']),
    {
      from: { element: { type: 'feature', captured: { feature: '*' } } },
      allow: { to: { element: { type: 'feature', captured: { feature: '{{ from.captured.feature }}' } } } },
    },
    ...edges.map(([from, to]) => ({
      from: { element: { type: 'feature', captured: { feature: from } } },
      allow: { to: featureIndex(to) },
    })),
    // External packages (checkAllOrigins): any package, except @tauri-apps outside platform/shell/tauri.ts
    // and alias-looking names (`@/`, `~/`, `src/`), which would be our own files under another name;
    // domain may use only decimal.js and temporal-polyfill.
    {
      from: { element: { type: '!domain' } },
      allow: {
        to: { module: { origin: ['external', 'core'], source: '!{@tauri-apps/**,@/**,~/**,src,@,~}' } },
      },
    },
    {
      from: { element: { type: 'shell-tauri' } },
      allow: { to: { module: { origin: 'external', source: '@tauri-apps/**' } } },
    },
    {
      from: { element: { type: 'domain' } },
      allow: { to: { module: { origin: 'external', source: ['decimal.js', 'temporal-polyfill'] } } },
    },
  ]
}

export function boundariesConfig(edges = FEATURE_EDGES) {
  return [
    {
      // every script kind under src/ (S0018 verifier F2), not only .ts and .vue
      files: ['src/**/*.{ts,mts,cts,tsx,js,mjs,cjs,jsx,vue}'],
      plugins: { boundaries },
      settings: {
        'boundaries/elements': elements,
        'import/resolver': { node: { extensions: ['.ts', '.mts', '.tsx', '.vue', '.js', '.mjs'] } },
      },
      rules: {
        // checkUnknownLocals: a local import the resolver cannot place is refused, not skipped (S0018 verifier F1).
        // Deliberate consequences, pinned by tests: write `./clock`, not `./clock.js`, for clock.ts; Vite query
        // imports (`?url`, `?raw`, `?worker`) are refused until the first slice that needs one teaches the map to
        // resolve them (BL-0056).
        'boundaries/dependencies': [
          'error',
          { default: 'disallow', checkAllOrigins: true, checkUnknownLocals: true, policies: policies(edges) },
        ],
        'boundaries/no-unknown-files': 'error',
      },
    },
  ]
}

export default boundariesConfig()
