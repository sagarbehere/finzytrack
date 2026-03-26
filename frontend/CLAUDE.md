# Frontend Rules

Vue 3 + Tailwind CSS + HeadlessUI + HeroIcons + TanStack Vue Table + ECharts.

## UI Style Rules (MANDATORY)

All UI work MUST follow the Tailwind Plus style system. **Do not invent custom styles.**

### References
- **Style guide**: `../ignore/tailwind-plus/STYLE_GUIDE.md` — exact class strings for every element type
- **Vue snippets**: `../ignore/tailwind-plus/vue/` — 364 reference components organized by category

### Quick rules
- **Tailwind version**: v4. Uses `@tailwindcss/vite` plugin, NOT PostCSS. No `tailwind.config.js`.
- **Primary color**: indigo-600 (dark: indigo-500). NOT blue-600.
- **Input borders**: `outline-1 -outline-offset-1 outline-gray-300` (NOT `border border-gray-300`)
- **Card containers**: `overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10`
- **Dark input bg**: `dark:bg-white/5` (NOT `dark:bg-gray-700`)
- **Dark card bg**: `dark:bg-gray-800/50` (NOT `dark:bg-gray-800`)
- **Secondary buttons**: Use `inset-ring inset-ring-gray-300` (NOT `border border-gray-300` or `ring-1 ring-inset`)
- **Shadows**: `shadow-xs` for buttons/inputs, `shadow-sm` for cards, `shadow-xl` for modals
- **Buttons**: Use exact TW+ button classes from style guide (primary/secondary/soft/danger)
- **Tabs**: Underline style for page-level, pill style for filters/toggles
- **Modals**: HeadlessUI Dialog with TW+ backdrop/panel/transition patterns
- **Focus states**: `focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600`
- Every element needs `dark:` variants per the style guide

### Do NOT
- Use `border` for input/card outlines — use `outline-*` or `ring-*` utilities
- Use blue-500/blue-600 — use indigo-600/indigo-500
- Create custom tab implementations — use the TW+ underline or pill patterns
- Invent new button styles — pick from primary/secondary/soft/danger in the style guide
- Skip dark mode

## Generated API Client (MANDATORY)

All backend API calls MUST use the generated client in `src/services/generated-api/`:
- Use generated service classes (`AccountsService`, `ConfigService`, `ImportService`, `RecipesService`, `LedgerService`, etc.)
- **Never use `fetch()` for backend API calls** except:
  1. **SSE streaming endpoints** — generated client can't handle `text/event-stream`; use `fetch()` + `ReadableStream`
  2. **External APIs** — user-configured services (e.g. LLM endpoints)
- Type checking: `npx vue-tsc --noEmit`
- API codegen: `npm run generate-api` (backend must be running with latest code)

## Error Handling (MANDATORY)

- **API errors**: Call `errorHandler.display(error)` from `@/utils/ErrorHandler` — routes errors to notification panel based on error code
- **Inline display**: Composables may also set a local `error` ref (`string | null`) for components to show inline. This is *in addition to* `errorHandler.display()`, not instead of it.
- **Do NOT** manually parse `ApiError.body` to extract messages — `errorHandler.display()` does this
- **Do NOT** use only `console.error()` for API failures — always call `errorHandler.display()`
- **Client-side errors** (file parsing, pre-API validation): Set local error ref for inline display only — these aren't `ApiError` instances
