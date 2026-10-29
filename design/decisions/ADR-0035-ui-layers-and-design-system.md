# ADR-0035: UI layers and design system: shadcn/ui on Radix plus our own domain primitives

This record decides the UI component layers, the theme, accessibility rules and lint guards.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** frontend, UI, accessibility

## Context

v0.3 pages looked alike but did not behave alike: rules standardized class strings, not components. There were 16 dialog shells, 5 confirmation patterns, 5 unsaved-change guards with different coverage, about 19 select implementations and 8 tab bars. Confirm dialogs focused the destructive button, account pickers accepted free text, grey helper text failed contrast, and no lint rule ran anywhere (review:17).

## Decision

(owner, 2026-10-24) The design is framework-neutral; names below are for React, with the Vue fallback (ADR-0006) in brackets.
- **Two layers.**
  - `components/ui/`: vendored **shadcn/ui on Radix** [shadcn-vue on Reka UI], unedited copies that touch no data.
  - `components/app/`: our domain primitives: `AccountCombobox`/`CommodityCombobox` (no free text), `MoneyText`/`MoneyCell`/`MoneyInput`, `DateField`/`DateRangeField`, `useConfirm` + one `ConfirmHost`, `DialogShell`/`SheetShell` with `dirty` and `busy`, **one `useUnsavedGuard(dirty)`** (route leave, in-view tab switch, reload, dialog close), `Field` + `useFieldErrors`, `QueryState`/`EmptyState`/`ErrorState`, `PageHeader`/`PageTabs`, `IconButton` (label required), `FileDropZone`/`ServerFilePicker`, `DataTable`, `SafeMarkdown`. These may read the shared catalog query hooks; mutations and view-specific fetches stay out.
- **Safe focus:** confirm dialogs focus Cancel (INV-16).
- **Pickers** pick existing accounts only (INV-15). **Confirmed by the owner** (2026-10-25, "I'm accepting your recommendations/actions on questions 5-11." (owner, 2026-10-25, in session)): a **"New account…"** action inside the picker opens the normal account form **blank** (nothing typed is carried over); nothing is created until Save, and then the new account is selected.
- **Theme:** CSS-variable tokens with one dark-mode source and a pre-paint script (it parses the JSON-encoded stored theme), so the first frame is right. Today's indigo/gray look is kept; the one visible change is darker helper text to meet WCAG AA. A token contrast test checks page and card surfaces in both modes.
- **Toasts:** `sonner` [vue-sonner], mounted outside anything a dialog makes inert, with a test that a toast raised inside a dialog is announced and clickable.
- **Icons:** Lucide (`lucide-react` [`@lucide/vue`]).
- **Tables:** TanStack Table v9, one column model, the WAI-ARIA grid keyboard model for the transaction grid. **Forms:** TanStack Form for the larger forms. **DateField v1** wraps the native date input with a `Temporal.PlainDate` value; the shadcn calendar is used only in the range-preset popover.
- **Guards:** ESLint with `eslint-plugin-jsx-a11y` [`eslint-plugin-vuejs-accessibility`] and the react-hooks strict rules; non-mutating `lint` and a separate `lint:fix`; format and lint of staged files run in the pre-commit hook; types and changed-area tests in the pre-push hook; everything in CI (PROCESS.md §6); bans on raw `<button>/<input>/<table>`, `confirm()` and `toFixed` outside `ui/` and `app/` (with an exemption for the viz registry).
- **Accessibility target:** WCAG 2.2 AA (INV-18), with axe in acceptance tests.

## Alternatives considered and rejected

- **HeadlessUI and class-string rules** (legacy). Rejected: behaviour copies drift.
- **Keeping HeroIcons.** Rejected: the shadcn CLI offers no HeroIcons option; every vendored file would need editing.
- **Free-text account entry** or auto-created accounts. Rejected (INV-15).
- **A segmented date field now.** Deferred: it can be swapped in behind the same interface.

## Consequences

- The new app builds these primitives first, pickers before the dialogs that contain them.
- `frontend/CLAUDE.md` names components, not class strings.

## Amendment (2026-10-29): the frontend is Vue 3

Owner: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it." (2026-10-29, in session; S0056)
- The frontend is Vue 3 (ADR-0053); the bracketed Vue names in the Decision apply, as ARCHITECTURE.md §3 and §10 list them: `components/ui/` holds vendored **shadcn-vue on Reka UI** (from the shadcn-vue CLI); toasts use **vue-sonner**; icons **`@lucide/vue`**; tables and forms TanStack **Vue** Table v9 and **Vue** Form.
- `useConfirm`, `useUnsavedGuard` and `useFieldErrors` are composables; `components/app/` may read the shared catalog query composables.
- The guards are `eslint-plugin-vue` and **`eslint-plugin-vuejs-accessibility`** recommended rules with typescript-eslint strict type-checked rules and `--max-warnings 0`, and `vue-tsc --noEmit` as the type gate (ARCHITECTURE.md §3.1); the React hooks rules have no Vue counterpart and are not run.
- Nothing else changes: the two layers, safe focus, pickers, theme, the bans and the accessibility target.

## References

review:17 §0–§4; review:25 §3.1; ADR-0006, ADR-0036; INV-15, INV-16, INV-18.
