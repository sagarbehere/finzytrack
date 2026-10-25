// @vitest-environment node
// INV-12 (BL-0012): the frontend's dependency direction (ARCHITECTURE.md §3.1, Vue reading; EP-07 for the
// Tauri import). Each rule is checked on a scratch tree outside the repo: planted imports that the map
// forbids must be refused, and the allowed directions must pass (EP-29).
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

import { ESLint, type Linter } from 'eslint'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { boundariesConfig } from '../../eslint.boundaries.js'

// One empty module per element, so every import below resolves to a real file.
const TREE = [
  'src/main.ts',
  'src/app/router.ts',
  'src/features/accounts/index.ts',
  'src/features/accounts/views/List.vue',
  'src/features/accounts/hooks/useTree.ts',
  'src/features/transactions/index.ts',
  'src/features/transactions/hooks/useGrid.ts',
  'src/catalog/accounts.ts',
  'src/components/app/MoneyText.vue',
  'src/components/ui/button.ts',
  'src/api/client.ts',
  'src/api/generated/types.ts',
  'src/platform/clock.ts',
  'src/platform/shell/tauri.ts',
  'src/platform/shell/browser.ts',
  'src/domain/money.ts',
  'src/domain/dates.ts',
]

// Stub packages, so external imports resolve as they do in the real tree.
const PACKAGES = ['vue', '@tauri-apps/api', 'decimal.js', 'temporal-polyfill']

let root = ''

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'ft-boundaries-'))
  for (const rel of TREE) {
    mkdirSync(dirname(join(root, rel)), { recursive: true })
    writeFileSync(join(root, rel), rel.endsWith('.vue') ? '<template><p /></template>\n' : 'export {}\n')
  }
  for (const name of PACKAGES) {
    const dir = join(root, 'node_modules', name)
    mkdirSync(join(dir, 'core'), { recursive: true })
    writeFileSync(join(dir, 'package.json'), JSON.stringify({ name, version: '0.0.0', main: 'index.js' }))
    writeFileSync(join(dir, 'index.js'), 'module.exports = {}\n')
    writeFileSync(join(dir, 'core', 'index.js'), 'module.exports = {}\n')
  }
})

afterAll(() => {
  rmSync(root, { recursive: true, force: true })
})

async function boundaryErrors(file: string, code: string, edges: [string, string][] = []): Promise<string[]> {
  const config: Linter.Config[] = [
    ...pluginVue.configs['flat/base'],
    { files: ['**/*.ts', '**/*.vue'], languageOptions: { parserOptions: { parser: tseslint.parser } } },
    { files: ['**/*.ts'], languageOptions: { parser: tseslint.parser } },
    ...boundariesConfig(edges),
    { settings: { 'boundaries/root-path': root } },
  ]
  const eslint = new ESLint({ cwd: root, overrideConfigFile: true, overrideConfig: config })
  const [result] = await eslint.lintText(code, { filePath: join(root, file) })
  return (result?.messages ?? []).map((m) => `${m.ruleId ?? 'parse-error'}: ${m.message}`)
}

const refused = async (file: string, code: string) => {
  const errors = await boundaryErrors(file, code)
  expect(
    errors.some((e) => e.startsWith('boundaries/')),
    `${file}: ${code} passed`,
  ).toBe(true)
}
const accepted = async (file: string, code: string, edges: [string, string][] = []) => {
  expect(await boundaryErrors(file, code, edges)).toEqual([])
}

describe('[INV-12] frontend dependency direction', () => {
  it.each([
    ['domain importing platform', 'src/domain/money.ts', "import '../platform/clock'\n"],
    ['domain importing api', 'src/domain/money.ts', "import '../api/client'\n"],
    ['domain importing vue', 'src/domain/money.ts', "import 'vue'\n"],
    ['platform importing api', 'src/platform/clock.ts', "import '../api/client'\n"],
    ['api importing a feature', 'src/api/client.ts', "import '../features/accounts'\n"],
    ['catalog importing components', 'src/catalog/accounts.ts', "import '../components/app/MoneyText.vue'\n"],
    ['components/ui importing domain', 'src/components/ui/button.ts', "import '../../domain/money'\n"],
    [
      'components/app importing api',
      'src/components/app/MoneyText.vue',
      '<script setup lang="ts">\nimport \'../../api/client\'\n</script>\n',
    ],
    ['a feature importing app', 'src/features/accounts/hooks/useTree.ts', "import '../../../app/router'\n"],
    [
      'a feature importing api/generated',
      'src/features/accounts/hooks/useTree.ts',
      "import '../../../api/generated/types'\n",
    ],
    [
      'a feature reaching into another feature',
      'src/features/accounts/hooks/useTree.ts',
      "import '../../transactions/hooks/useGrid'\n",
    ],
    [
      'a feature importing another feature index without an edge',
      'src/features/accounts/hooks/useTree.ts',
      "import '../../transactions'\n",
    ],
    ['@tauri-apps outside platform/shell/tauri.ts', 'src/platform/shell/browser.ts', "import '@tauri-apps/api/core'\n"],
    ['@tauri-apps in a feature', 'src/features/accounts/hooks/useTree.ts', "import '@tauri-apps/api/core'\n"],
    ['a file outside the map', 'src/stray/thing.ts', 'export const x = 1\n'],
    // S0018 verifier F1, F2, F4: unresolved local specifiers, other script kinds, alias-looking names
    ['a domain import written with .js for a .ts file', 'src/domain/money.ts', "import '../platform/clock.js'\n"],
    ['an import of a file that does not exist', 'src/domain/money.ts', "import './nowhere'\n"],
    ['a stray .js file', 'src/stray/thing.js', 'export const x = 1\n'],
    ['a stray .mts file', 'src/stray/thing.mts', 'export const x = 1\n'],
    ['an .mts file in domain importing platform', 'src/domain/evil.mts', "import '../platform/clock'\n"],
    ['an alias-looking @/ import', 'src/features/accounts/hooks/useTree.ts', "import '@/app/router'\n"],
    ['an alias-looking src/ import', 'src/features/accounts/hooks/useTree.ts', "import 'src/app/router'\n"],
    ['an alias-looking ~/ import', 'src/features/accounts/hooks/useTree.ts', "import '~/app/router'\n"],
    // every script kind under src/ is linted (round-2 F2)
    ...['tsx', 'cts', 'jsx', 'mjs', 'cjs'].map((ext): [string, string, string] => [
      `a .${ext} file in domain importing platform`,
      `src/domain/evil.${ext}`,
      "import '../platform/clock'\n",
    ]),
    // deliberate, until BL-0056: Vite query imports are refused (round-2 F1)
    ['a Vite ?url import', 'src/features/accounts/hooks/useTree.ts', "import '../views/List.vue?url'\n"],
    ['a Vite ?raw import', 'src/domain/money.ts', "import './dates?raw'\n"],
    // F3: the other forbidden pairs of the map
    ['app deep-importing a feature', 'src/app/router.ts', "import '../features/accounts/views/List.vue'\n"],
    ['app importing api/generated', 'src/app/router.ts', "import '../api/generated/types'\n"],
    ['catalog importing a feature', 'src/catalog/accounts.ts', "import '../features/accounts'\n"],
    ['catalog importing components/ui', 'src/catalog/accounts.ts', "import '../components/ui/button'\n"],
    [
      'components/app importing platform',
      'src/components/app/MoneyText.vue',
      '<script setup lang="ts">\nimport \'../../platform/clock\'\n</script>\n',
    ],
    [
      'components/app importing a feature',
      'src/components/app/MoneyText.vue',
      '<script setup lang="ts">\nimport \'../../features/accounts\'\n</script>\n',
    ],
    ['components/ui importing catalog', 'src/components/ui/button.ts', "import '../../catalog/accounts'\n"],
    ['api/generated importing api', 'src/api/generated/types.ts', "import '../client'\n"],
    ['platform importing a feature', 'src/platform/clock.ts', "import '../features/accounts'\n"],
    ['platform/shell/tauri.ts importing api', 'src/platform/shell/tauri.ts', "import '../../api/client'\n"],
    ['domain importing catalog', 'src/domain/money.ts', "import '../catalog/accounts'\n"],
  ])('refuses %s', async (_label, file, code) => {
    await refused(file, code)
  })

  it.each([
    [
      'app importing a feature index and the kernel',
      'src/app/router.ts',
      "import '../features/accounts'\nimport '../platform/clock'\nimport '../domain/money'\n",
    ],
    [
      'a feature importing its own files and the kernel layers',
      'src/features/accounts/hooks/useTree.ts',
      "import '../views/List.vue'\nimport '../../../catalog/accounts'\nimport '../../../api/client'\nimport '../../../domain/money'\n",
    ],
    [
      'components/app importing ui, catalog and domain',
      'src/components/app/MoneyText.vue',
      "<script setup lang=\"ts\">\nimport '../ui/button'\nimport '../../catalog/accounts'\nimport '../../domain/money'\n</script>\n",
    ],
    [
      'api importing generated, platform and domain',
      'src/api/client.ts',
      "import './generated/types'\nimport '../platform/clock'\nimport '../domain/money'\n",
    ],
    [
      'domain importing decimal.js, temporal-polyfill and itself',
      'src/domain/money.ts',
      "import 'decimal.js'\nimport 'temporal-polyfill'\nimport './dates'\n",
    ],
    ['platform/shell/tauri.ts importing @tauri-apps', 'src/platform/shell/tauri.ts', "import '@tauri-apps/api/core'\n"],
    [
      'a feature importing components/app',
      'src/features/accounts/hooks/useTree.ts',
      "import '../../../components/app/MoneyText.vue'\n",
    ],
  ])('accepts %s', async (_label, file, code) => {
    await accepted(file, code)
  })

  it('refuses Node core modules in domain', async () => {
    await refused('src/domain/money.ts', "import 'node:fs'\n")
  })

  describe('a listed cross-feature edge', () => {
    const edges: [string, string][] = [['accounts', 'transactions']]
    const from = 'src/features/accounts/hooks/useTree.ts'

    it('allows the other feature through its index only', async () => {
      await accepted(from, "import '../../transactions'\n", edges)
      expect(
        (await boundaryErrors(from, "import '../../transactions/hooks/useGrid'\n", edges)).some((e) =>
          e.startsWith('boundaries/'),
        ),
      ).toBe(true)
    })

    it('does not open the reverse direction', async () => {
      const errors = await boundaryErrors(
        'src/features/transactions/hooks/useGrid.ts',
        "import '../../accounts'\n",
        edges,
      )
      expect(errors.some((e) => e.startsWith('boundaries/'))).toBe(true)
    })
  })
})
