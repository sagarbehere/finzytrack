# ADR-0035: UI layers and design system: shadcn/ui on Radix plus our own domain primitives

This record decides the UI component layers, the theme, accessibility rules and lint guards.

- **Status:** Accepted. Amended 2026-10-29 (Vue 3), 2026-11-28 (recorded type-gate patches in vendored copies) and 2026-11-28 (the look: one theme file, no user theming; S0170).
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

## Amendment (2026-11-28): recorded type-gate patches in vendored copies

Owner, 2026-11-28, in session: "Patch + record (Recommended)", on `design/inbox/answered/20261128-vendored-ui-strict-gates.md`. The option as put to the owner: "Change that one line (same behaviour), list it in `components/ui/PATCHES.md` so it is re-applied on every re-copy, and amend ADR-0035: vendored copies are unedited except for recorded type-gate patches. Prettier and two style-only lint rules are switched off in `components/ui/` only; every type, accessibility and layer rule stays on." (S0169)
- **Replaces "unedited copies"** with: copies are byte for byte what the shadcn-vue CLI writes, except the patches listed in `src/components/ui/PATCHES.md`. A patch exists only to make a copy pass the type gate, keeps the copy's behaviour, and is re-applied whenever the component is copied again. The first one: the input's `useVModel` call passes `defaultValue` only when it is defined, since `exactOptionalPropertyTypes` refuses an option present with the value `undefined`.
- **Gate scoping for `src/components/ui/` only:** prettier skips it, and ESLint turns off `vue/multi-word-component-names` and `vue/require-default-prop` there (shadcn-vue names its components `Button`, `Input` and leaves optional props without defaults). Every other rule stays on there, type-checked, accessibility and layer rules included; `tests/unit/gates.test.ts` pins that only those two rules differ.
- The `data-*` attributes the copies set (`data-slot`, `data-variant`, `data-size`) are declared in our own type file (`src/app/vendored-ui.d.ts`), not by a patch.

## Amendment (2026-11-28): the look, one theme file, no user theming

Owner, 2026-11-28, in session, on a design canvas (S0170; the words in full in `design/journal/S0170-design-foundations.md`): "We can take a new direction. We don't need to adhere to v0.3's look"; "the v0.3 1 Refined's sidebar and top bar are to my liking. They also match the existing v0.3 that I like. I like the table of v0.3 3 over v0.3 1's table."; "Yes, Balance links should be neutral, I think. Density comfortable. We want full support for Dark mode from Day 1"; on theming: "I don't think I or other users will spend time changing the theme.. it's a finance app after all, for serious users", and on the recommendation below: "Yes, take that into S0170's brief".
- **Replaces "Today's indigo/gray look is kept"** with: the look is the owner's chosen board ("v0.3 · 4 Synthesis": cool slate surfaces, an indigo accent, v0.3's sidebar and top bar refined, sentence-case tables in a faintly outlined card, neutral balance links), written down in `design/specs/visual-language.md`. Its values move only where WCAG 2.2 AA needs more (INV-18; the spec's §14).
- **One theme file:** `frontend/src/app/theme.css` is the only place a colour, size, radius or shadow value is written. Components use token names only; a lint rule (`ft/no-raw-colour`, `frontend/scripts/raw-colours.mjs`) refuses raw colours, Tailwind palette classes and faded text tokens anywhere else under `src`. The vendored copies in `components/ui` are exempt from that one rule, as from the two style rules: they are the CLI's bytes and keep shadcn-vue's own classes (`text-white` on the destructive button).
- **No user theming:** light, dark or system, nothing else; no theme files and no theme editor. Dashboard colour themes (CAP-DASH-THEMES) colour charts only.
- **Visual language and information design are separate** (owner: "I like your suggestions."): restyling changes presentation only, and no information element v0.3 shows is dropped without the owner ("No, that isn't good.. because I'll need all the information elements that are now missing in your update"). Each vertical's brief starts with a design step: the owner sees "faithful" and "re-imagined" (ADR-0043) versions on a canvas and picks per screen (roadmap §3.3).
- **Rejected along the way** (owner, same session): option C ("it looks a bit like a toy (not serious) look"); "v0.3 2" ("it looks .. just cluttered.."); a cream background with indigo ("I think the Indigo accents go well with the bluish/grayish/slate kind of background colors"); a restyle that dropped columns.
- The rest of this ADR stands: two layers, safe focus, pickers, the bans, the accessibility target, and the token contrast test, which now covers every pair the look draws.

## References

review:17 §0–§4; review:25 §3.1; ADR-0006, ADR-0036; INV-15, INV-16, INV-18.
