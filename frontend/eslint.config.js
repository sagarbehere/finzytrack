// Lint for frontend/ (ARCHITECTURE.md §10; frontend/CLAUDE.md). Warnings fail: --max-warnings 0.
import pluginVue from 'eslint-plugin-vue'
import vueA11y from 'eslint-plugin-vuejs-accessibility'
import globals from 'globals'
import tseslint from 'typescript-eslint'

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
      // INV-07: there is no UTC or system "today"; dates come from src/domain (Temporal).
      'no-restricted-syntax': [
        'error',
        {
          selector: "NewExpression[callee.name='Date'][arguments.length=0]",
          message: 'INV-07: no system "today"; use src/domain dates',
        },
        {
          selector: "CallExpression[callee.object.name='Date'][callee.property.name='now']",
          message: 'INV-07: no system "today"; use src/domain dates',
        },
      ],
      // INV-06: money is never a float string.
      'no-restricted-properties': [
        'error',
        { property: 'toFixed', message: 'INV-06: format money through src/domain' },
      ],
    },
  },
)
