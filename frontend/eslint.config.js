// Lint for frontend/ (ARCHITECTURE.md §10; frontend/CLAUDE.md). Warnings fail: --max-warnings 0.
import prettierOff from 'eslint-config-prettier/flat'
import pluginVue from 'eslint-plugin-vue'
import vueA11y from 'eslint-plugin-vuejs-accessibility'
import globals from 'globals'
import tseslint from 'typescript-eslint'

import boundaries from './eslint.boundaries.js'

const today = 'INV-07: no system "today" or clock; use src/domain dates'
// Deliberate aliasing (`const D = Date; new D()`) is left to review (BL-0007).
const systemTime = [
  { selector: "NewExpression[callee.name='Date'][arguments.length=0]", message: today },
  { selector: "CallExpression[callee.name='Date']", message: today },
  { selector: "CallExpression[callee.object.name='Date'][callee.property.name='now']", message: today },
  { selector: "NewExpression[callee.property.name='Date'][arguments.length=0]", message: today },
  { selector: "CallExpression[callee.property.name='Date']", message: today },
  { selector: "CallExpression[callee.object.property.name='Date'][callee.property.name='now']", message: today },
]

// L9.M-7: tests stub components by import (vi.mock of the module, findComponent(ImportedComponent)), never by
// name: a name-keyed stub or lookup keeps passing after the component is renamed and silently tests nothing.
const byName = 'L9.M-7: stub by import (vi.mock the module; findComponent(Imported)), never by component name'
// Deliberate indirection (a variable key, Reflect.set, Object.defineProperty, a selector held in a variable) is left
// to review, like the INV-07 aliasing above.
const finders = '/^(find|get)(All)?Components?$/'
const stubsByName = [
  { selector: "Property[key.name='stubs']", message: byName },
  { selector: "Property[key.type='TemplateLiteral'][key.quasis.0.value.raw='stubs']", message: byName },
  {
    selector: "MemberExpression[property.type='TemplateLiteral'][property.quasis.0.value.raw='stubs']",
    message: byName,
  },
  { selector: "Property[key.value='stubs']", message: byName },
  { selector: "MemberExpression[property.name='stubs']", message: byName },
  { selector: "MemberExpression[property.value='stubs']", message: byName },
  {
    selector: `CallExpression[callee.property.name=${finders}] > ObjectExpression > Property[key.name='name']`,
    message: byName,
  },
  {
    selector: `CallExpression[callee.property.name=${finders}] > ObjectExpression > Property[key.value='name']`,
    message: byName,
  },
]
const testFiles = ['tests/**/*.ts', 'src/**/*.test.ts', 'src/**/*.spec.ts']

export default tseslint.config(
  { ignores: ['dist/**', 'playwright-report/**', 'test-results/**'] },
  ...tseslint.configs.strictTypeChecked,
  ...pluginVue.configs['flat/recommended'],
  ...vueA11y.configs['flat/recommended'],
  // prettier owns formatting: the presets' layout rules (vue/max-attributes-per-line and the like) are off, or
  // `eslint --fix` and `prettier --write` undo each other (S0032). Our own rules below are not formatting rules.
  prettierOff,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        parser: tseslint.parser,
        projectService: true,
        extraFileExtensions: ['.vue'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
  // INV-12: the layer map (eslint.boundaries.js).
  ...boundaries,
  {
    rules: {
      // INV-07 (pending, BL-0007): there is no UTC or system "today"; dates come from src/domain (Temporal).
      'no-restricted-syntax': ['error', ...systemTime],
      // INV-06 (pending, BL-0006): money is never a float string.
      'no-restricted-properties': [
        'error',
        { property: 'toFixed', message: 'INV-06: format money through src/domain' },
      ],
      // The same bans inside Vue templates, where display formatting happens.
      'vue/no-restricted-syntax': [
        'error',
        ...systemTime,
        {
          selector: "CallExpression[callee.property.name='toFixed']",
          message: 'INV-06: format money through src/domain',
        },
      ],
    },
  },
  {
    // INV-24 (S0030): every e2e file but the harness's own fixtures module (and the nested self-test's config) takes
    // `test` and `expect` from the acceptance harness, so every flow runs against its own sidecar with the request
    // guard, the frozen clock and the hard failures armed, and no helper can hand out an unguarded `test`.
    files: ['e2e/**/*.ts'],
    ignores: ['e2e/harness/fixtures.ts', 'e2e/harness/selftest/playwright.config.ts'],
    rules: {
      '@typescript-eslint/no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@playwright/test',
              message: 'INV-24: import test and expect from e2e/harness/fixtures (types may come from here)',
              allowTypeImports: true,
            },
          ],
        },
      ],
    },
  },
  {
    // INV-24 (S0030): specs do not open their own browser contexts or pages (the guard arms those it sees, but a
    // spec has no reason to); the harness's self-test opens them on purpose to prove the guard. Later config
    // replaces a rule's options, so the INV-06 ban is repeated.
    files: ['e2e/**/*.ts'],
    ignores: ['e2e/harness.spec.ts', 'e2e/harness/fixtures.ts'],
    rules: {
      'no-restricted-properties': [
        'error',
        { property: 'toFixed', message: 'INV-06: format money through src/domain' },
        { object: 'browser', property: 'newContext', message: 'INV-24: use the `context` fixture' },
        { object: 'browser', property: 'newPage', message: 'INV-24: use the `page` fixture or context.newPage()' },
      ],
    },
  },
  {
    // Later config replaces a rule's options, so the INV-07 bans are repeated here with the stub bans.
    files: testFiles,
    rules: { 'no-restricted-syntax': ['error', ...systemTime, ...stubsByName] },
  },
)
