# FinzyTrack

Personal finance app: Beancount backend + Vue 3 frontend.

## UI Style Rules (MANDATORY)

All UI work MUST follow the Tailwind Plus style system. **Do not invent custom styles.**

### References
- **Style guide**: `ignore/tailwind-plus/STYLE_GUIDE.md` — exact class strings for every element type
- **Vue snippets**: `ignore/tailwind-plus/vue/` — 364 reference components organized by category
- When building or modifying UI, consult the style guide first, then reference the Vue snippets for more complex patterns

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

### Do NOT
- Use `border` for input/card outlines — use `outline-*` or `ring-*` utilities
- Use blue-500/blue-600 — use indigo-600/indigo-500
- Create custom tab implementations — use the TW+ underline or pill patterns
- Invent new button styles — pick from primary/secondary/soft/danger in the style guide
- Skip dark mode — every element needs `dark:` variants per the style guide

## Tech Stack
- Frontend: Vue 3 + Tailwind CSS + HeadlessUI + HeroIcons + TanStack Vue Table + ECharts
- Backend: Python + Beancount + SQLite
- Type checking: `npx vue-tsc --noEmit` from `frontend/`
- API codegen: `npm run generate-api` from `frontend/` (for non-streaming endpoints only)
