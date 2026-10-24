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
- **Pickers** pick existing accounts only (INV-15). **Pending owner confirmation** (review:17 Q3 was accepted as answered, but the answer was phrased as an option): a **"New account…"** action inside the picker opens the normal account form **blank** (nothing typed is carried over); nothing is created until Save, and then the new account is selected.
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

## References

review:17 §0–§4; review:25 §3.1; ADR-0006, ADR-0036; INV-15, INV-16, INV-18.
