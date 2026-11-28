# The vendored shadcn-vue input fails our strict type gate: patch one line, or check vendored files less strictly?
- Asked: 2026-11-28 by the implementer of S0169-m2-ui-primitives
- blocks: S0169-m2-ui-primitives (the input primitive and the hand-off; the theme, CSP, button and icons do not depend on it)
- Rubric item: 4 (changing an ADR: ADR-0035 says `components/ui/` holds unedited copies) and "loosening any gate" (always the owner's)
- ADRs checked: ADR-0035 (unedited copies; says nothing about how they meet our gates), ADR-0053, ARCHITECTURE.md §3.1 (strict type-checked lint, `vue-tsc` with `strictTemplates`, `exactOptionalPropertyTypes` in the pinned `tsconfig.json`). None says what happens when an unedited copy fails a gate.

## Context (plain language, a concrete example first)
The owner installed the packages and I copied shadcn-vue 2.9.0's `button` and `input` into `frontend/src/components/ui/` byte for byte. Three gates object:

1. **Type gate (`vue-tsc`), the one that needs you.** `Input.vue` passes `defaultValue: props.defaultValue` to VueUse's `useVModel`. When the caller gives no default, that value is `undefined`. Our `tsconfig.json` turns on `exactOptionalPropertyTypes`, which says "an optional field may be missing, but may not be present with the value `undefined`". VueUse's option type does not allow `undefined`, so the type gate fails. The code works at run time; it is a strictness mismatch, not a bug.
   - The other type errors (the `data-slot`, `data-variant`, `data-size` attributes, unknown to `strictTemplates`) go away with a small type declaration in our own file, adding exactly those three attribute names. That needs no gate change and no edit, and I will do it unless you object.
   - Leaving the input out of the type gate does not work: TypeScript checks every file a checked file imports, so the error comes back as soon as anything uses the input.
2. **Lint (style rules only).** `vue/multi-word-component-names` (a component called `Button` should be a two-word name) and `vue/require-default-prop` (optional props need a default) fail on both files. No type-safety, accessibility or layer rule fails.
3. **Prettier.** The copies use double quotes and no trailing commas; prettier would rewrite them, which is an edit.

## Options (cheapest first; PROCESS.md §5)
1. **Patch the one line, record the patch** (ADR-0035 amendment: "unedited, except the recorded patches that make a copy pass our type gate"). The line becomes `...(props.defaultValue === undefined ? {} : { defaultValue: props.defaultValue }),`, which behaves the same; checked: with it and the attribute declaration, `vue-tsc` passes. The patch is listed in `src/components/ui/PATCHES.md` and re-applied when the input is vendored again. Plus: the style rules above off and prettier off for `src/components/ui/` only (every other rule, the type gate and the layer map stay on there). Cost: one recorded line to re-apply on an update.
2. **Write our own input instead of vendoring it**, in the same place, with shadcn-vue's classes, under every rule (no `useVModel`; Vue's `defineModel`). Plus the same lint and prettier scoping for the vendored button. Cost: one file that no longer follows shadcn-vue updates; `components/ui` becomes "vendored, except the input".
3. **Check vendored files through a declaration boundary**: `components/ui` becomes its own TypeScript project, built first without `exactOptionalPropertyTypes`, and the app sees only its emitted type declarations. Keeps the copies unedited and the app's checks unchanged. Cost: a second tsconfig, `vue-tsc --build` mode and a generated folder, for one line today.
4. **Turn `exactOptionalPropertyTypes` off for the whole frontend.** Cheapest to do, but loosens the type gate for all our own code. Not recommended.

## Recommendation
Option 1: one recorded line beats new build machinery (3) or a fork (2), and keeps every check on our own code as it is. The lint and prettier scoping is part of any vendoring option: an unedited copy cannot meet our style rules. Review history: none (new mechanism family).

## Answer (owner, verbatim, with date)
