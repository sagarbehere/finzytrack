// Lint for frontend/ (ARCHITECTURE.md §10; frontend/CLAUDE.md). Warnings fail: --max-warnings 0.
import prettierOff from 'eslint-config-prettier/flat'
import pluginVue from 'eslint-plugin-vue'
import vueA11y from 'eslint-plugin-vuejs-accessibility'
import globals from 'globals'
import tseslint from 'typescript-eslint'

import boundaries from './eslint.boundaries.js'

const today = 'INV-07: no system "today" or clock; use src/domain dates'
const floatParse = 'INV-06: money is never parsed as a float; parse it through src/domain'
// In src/ outside src/platform the Date class itself is refused (below, S0092), which closes aliasing there; in tests,
// e2e and tooling these selectors are the ban, and an alias (`const D = Date; new D()`) is left to review.
const systemTime = [
  { selector: "NewExpression[callee.name='Date'][arguments.length=0]", message: today },
  { selector: "CallExpression[callee.name='Date']", message: today },
  { selector: "CallExpression[callee.object.name='Date'][callee.property.name='now']", message: today },
  { selector: "NewExpression[callee.property.name='Date'][arguments.length=0]", message: today },
  { selector: "CallExpression[callee.property.name='Date']", message: today },
  { selector: "CallExpression[callee.object.property.name='Date'][callee.property.name='now']", message: today },
]

// S0092 verifier V3: a date formatter with no date formats now (the system clock), whatever its time zone; and the
// class reached by name through Reflect.
const clockByFormat = {
  selector: 'CallExpression[callee.property.name=/^(format|formatToParts)$/][arguments.length=0]',
  message: today,
}
const appClock = [
  clockByFormat,
  {
    selector: "CallExpression[callee.object.name='Reflect'][callee.property.name='get'][arguments.1.value='Date']",
    message: today,
  },
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
// S0032 verifier F1: a fixme test passes only while its planned module is missing (tests/fixme.ts), which works only
// when a failed assertion throws inside the body. `expect.soft` records a failure and `expect.assertions` /
// `expect.hasAssertions` are checked after the body, so a buggy first implementation would still count as the expected
// failure. Unit tests assert with plain `expect`.
const deferred =
  'S0032: a failed assertion must throw in the test body; use plain expect (no soft or counted assertions)'
const deferredAssertions = [
  {
    selector: "MemberExpression[object.name='expect'][property.name=/^(soft|assertions|hasAssertions)$/]",
    message: deferred,
  },
  {
    selector: "MemberExpression[object.name='expect'][property.value=/^(soft|assertions|hasAssertions)$/]",
    message: deferred,
  },
  {
    selector:
      "VariableDeclarator[init.name='expect'] > ObjectPattern > Property[key.name=/^(soft|assertions|hasAssertions)$/]",
    message: deferred,
  },
]
// EP-23, EP-03, INV-02 (S0104): ledger semantics come from the backend (ADR-0024: the frontend asks; a generated
// account regex was rejected). The frontend has no home for these concepts yet; the planned tolerance indicator
// (L3-09) adds its home here as an override and in scripts/concept_counters.py. These are the shapes
// scripts/concept_counters.py refuses in Python. Stated limits (EP-30; left to review): ESLint sees no names, so a
// pattern or a separator held in a variable, a template-literal `RegExp`, a sum held in a variable before it is
// tested, and a helper defined elsewhere are not seen.
const roots = '/^(Assets|Liabilities|Equity|Income|Expenses):?$/'
const ordering = '/^[<>]=?$/'
const kernel = (concept) => `EP-23: a second implementation of ${concept}; it comes from the backend (ADR-0024, INV-02)`
const kernelConcepts = [
  // account grammar: a regex on account names, a name taken apart or joined by hand
  {
    selector: String.raw`Literal[regex.pattern=/[^?]:|^:/][regex.pattern=/A-Z|p[\\]?[{]L|Assets|Liabilities|Equity|Income|Expenses/]`,
    message: kernel('the account grammar'),
  },
  {
    selector: String.raw`:matches(NewExpression, CallExpression)[callee.name='RegExp'][arguments.0.value=/[^?]:|^:/][arguments.0.value=/A-Z|p[\\]?[{]L|Assets|Liabilities|Equity|Income|Expenses/]`,
    message: kernel('the account grammar'),
  },
  {
    selector: "CallExpression[callee.property.name=/^(split|indexOf|lastIndexOf|join)$/][arguments.0.value=':']",
    message: kernel('the account grammar'),
  },
  // currency grammar: a regex of upper-case classes with a count
  {
    selector: String.raw`Literal[regex.pattern=/A-Z/][regex.pattern=/[{]/]:not([regex.pattern=/a-z/])`,
    message: kernel('the currency grammar'),
  },
  // account roots and kinds: a root name compared, matched or listed; a root option read
  { selector: `BinaryExpression > Literal[value=${roots}]`, message: kernel('account kinds') },
  { selector: `SwitchCase > Literal[value=${roots}]`, message: kernel('account kinds') },
  { selector: `ArrayExpression > Literal[value=${roots}]`, message: kernel('account kinds') },
  {
    selector: `CallExpression[callee.property.name=/^(startsWith|endsWith)$/] > Literal[value=${roots}]`,
    message: kernel('account kinds'),
  },
  {
    selector: 'Literal[value=/^name_(assets|liabilities|equity|income|expenses)$/]',
    message: kernel('the account roots'),
  },
  // tolerance: a power of ten from a scale, a tolerance option read
  {
    selector: "BinaryExpression[operator='**'][left.value=10]:not([right.type='Literal'])",
    message: kernel('the tolerance'),
  },
  {
    selector:
      "CallExpression[callee.object.name='Math'][callee.property.name='pow'][arguments.0.value=10]:not([arguments.1.type='Literal'])",
    message: kernel('the tolerance'),
  },
  {
    selector: 'Literal[value=/^(inferred_tolerance_default|inferred_tolerance_multiplier|infer_tolerance_from_cost)$/]',
    message: kernel('the tolerance'),
  },
  // balance check: a magnitude or a tolerance compared, a fraction as a fixed tolerance
  {
    selector: `BinaryExpression[operator=${ordering}] > CallExpression[callee.property.name='abs']`,
    message: kernel('the balance check'),
  },
  {
    selector: `BinaryExpression[operator=${ordering}] > Identifier[name=/^(tol|eps|epsilon)$|toleran/i]`,
    message: kernel('the balance check'),
  },
  {
    selector: `BinaryExpression[operator=${ordering}] > MemberExpression[property.name=/^(tol|eps|epsilon)$|toleran/i]`,
    message: kernel('the balance check'),
  },
  {
    selector: `BinaryExpression[operator=${ordering}] > Literal[value>0][value<1]`,
    message: kernel('the balance check'),
  },
  {
    selector:
      "CallExpression[callee.property.name=/^(lt|lte|gt|gte|lessThan|lessThanOrEqualTo|greaterThan|greaterThanOrEqualTo|cmp|comparedTo)$/][callee.object.callee.property.name='abs']",
    message: kernel('the balance check'),
  },
  // number parsing: a regex for digits and a point, grouping removed by hand
  {
    selector: String.raw`Literal[regex.pattern=/[\\][.]|[[][^\]]*[.][^\]]*[\]]/][regex.pattern=/[\\]d|0-9/]`,
    message: kernel('number parsing'),
  },
  {
    selector: "CallExpression[callee.property.name=/^replace(All)?$/][arguments.0.value=',']",
    message: kernel('number parsing'),
  },
  // S0104 verifier round 1 (F6): the decimal.js forms the planned tolerance indicator (L3-09) would be written in
  {
    selector: String.raw`CallExpression[callee.property.name=/^replace(All)?$/][arguments.0.regex.pattern=/^[[]?,[\]]?[+]?$/]`,
    message: kernel('number parsing'),
  },
  {
    selector:
      "CallExpression[callee.property.name='pow'][callee.object.type='NewExpression'][callee.object.arguments.0.value=10]:not([arguments.0.type='Literal'])",
    message: kernel('the tolerance'),
  },
  {
    selector: "CallExpression[callee.property.name='pow'][arguments.0.value=10]:not([arguments.1.type='Literal'])",
    message: kernel('the tolerance'),
  },
  {
    selector:
      ':matches(UnaryExpression, BinaryExpression, CallExpression[callee.property.name=/^(pow|shiftedBy|times|mul|div|toDecimalPlaces|toDP)$/]) > CallExpression[callee.property.name=/^(decimalPlaces|dp)$/]',
    message: kernel('the tolerance'),
  },
  {
    selector:
      "CallExpression[callee.property.name=/^(isZero|eq|equals)$/][callee.object.callee.property.name='reduce']",
    message: kernel('the balance check'),
  },
  {
    selector: "BinaryExpression[operator=/^[!=]==?$/] > CallExpression[callee.property.name='reduce']",
    message: kernel('the balance check'),
  },
  {
    selector: String.raw`Literal[regex.pattern=/^\^?([[]A-Z[^\]a-z]*[\]][*+?]?)+[$]?$/]`,
    message: kernel('the currency grammar'),
  },
  { selector: `Property[key.name=${roots}]`, message: kernel('account kinds') },
  { selector: `Property[key.value=${roots}]`, message: kernel('account kinds') },
  { selector: `Property[value.value=${roots}]`, message: kernel('account kinds') },
  { selector: `TSEnumMember[initializer.value=${roots}]`, message: kernel('account kinds') },
]
const srcFiles = ['src/**/*.{ts,mts,cts,tsx,js,mjs,cjs,jsx,vue}']
const srcTests = ['src/**/*.{test,spec}.{ts,mts,cts,tsx,js,mjs,cjs,jsx}']
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
    files: ['**/*.js', '**/*.mjs'],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    // Build tooling run by Node (frontend/CLAUDE.md: frontend/scripts/, .mjs; S0046: the API type generator).
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
  // INV-12: the layer map (eslint.boundaries.js).
  ...boundaries,
  {
    rules: {
      // INV-07: there is no UTC or system "today"; dates come from src/domain (Temporal).
      'no-restricted-syntax': ['error', ...systemTime],
      // INV-06: money is never a float string.
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
        // INV-07 (S0092): templates may not name the Date class (a field `row.Date` is not it), nor format now;
        // INV-06: nor parse money as a float.
        {
          selector: "Identifier[name='Date']:not(MemberExpression[computed=false] > Identifier.property)",
          message: today,
        },
        clockByFormat,
        ...kernelConcepts,
        {
          selector: "Identifier[name='parseFloat']:not(MemberExpression[computed=false] > Identifier.property)",
          message: floatParse,
        },
      ],
    },
  },
  {
    // INV-06, INV-07 (S0092, BL-0006, BL-0007): in app code the Date class itself is refused, so no alias of it
    // (`const D = Date; new D()`) can read the system clock; only src/platform, the clock adapter's home, names it
    // (the system-clock forms above stay refused there too). Temporal's system clock (`.Now`) and float parsing of
    // money are refused with it. Later config replaces a rule's options, so the toFixed ban is repeated.
    files: srcFiles,
    ignores: ['src/platform/**'],
    rules: {
      'no-restricted-syntax': ['error', ...systemTime, ...appClock, ...kernelConcepts],
      'no-restricted-globals': ['error', { name: 'Date', message: today }, { name: 'parseFloat', message: floatParse }],
      'no-restricted-properties': [
        'error',
        { property: 'toFixed', message: 'INV-06: format money through src/domain' },
        { object: 'globalThis', property: 'Date', message: today },
        { object: 'window', property: 'Date', message: today },
        { object: 'self', property: 'Date', message: today },
        { property: 'Now', message: today },
        { object: 'Number', property: 'parseFloat', message: floatParse },
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
    rules: { 'no-restricted-syntax': ['error', ...systemTime, ...stubsByName, ...deferredAssertions] },
  },
  {
    // EP-23 (S0104 verifier F5): every file under src, whatever its suffix, src/platform and src's own tests
    // included, gets the kernel bans. Later config replaces a rule's options, so each block repeats what it keeps.
    files: ['src/platform/**/*.{ts,mts,cts,tsx,js,mjs,cjs,jsx,vue}'],
    rules: { 'no-restricted-syntax': ['error', ...systemTime, ...kernelConcepts] },
  },
  {
    files: srcTests,
    rules: {
      'no-restricted-syntax': ['error', ...systemTime, ...stubsByName, ...deferredAssertions, ...kernelConcepts],
    },
  },
)
