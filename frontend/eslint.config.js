// Lint for frontend/ (ARCHITECTURE.md §10; frontend/CLAUDE.md). Warnings fail: --max-warnings 0.
import pluginVue from 'eslint-plugin-vue'
import vueA11y from 'eslint-plugin-vuejs-accessibility'
import globals from 'globals'
import tseslint from 'typescript-eslint'

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

export default tseslint.config(
  { ignores: ['dist/**', 'playwright-report/**', 'test-results/**'] },
  ...tseslint.configs.strictTypeChecked,
  ...pluginVue.configs['flat/recommended'],
  ...vueA11y.configs['flat/recommended'],
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
)
