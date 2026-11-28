# The type gate refuses ordinary HTML attributes on vendored components (`disabled`, `aria-label`, `id`): how should screens pass them?
- Asked: 2026-11-28 by the implementer of S0170-design-foundations
- blocks: nothing (S0170's dev-only gallery passes these attributes through a `v-bind` object meanwhile, BL-0326); the first screen slice of M2 meets it
- Rubric item: "loosening any gate" (always the owner's); 4 if ADR-0035's amendment of 2026-11-28 is widened
- ADRs checked: ADR-0035 and its amendment of 2026-11-28 (it declares only the copies' own `data-*` names, "only these names"); ARCHITECTURE.md §3.1 (`vue-tsc` with `strictTemplates`, pinned in `tests/unit/gates.test.ts`). None says how a screen passes a plain HTML attribute to a vendored component.

## Context (plain language, a concrete example first)
Writing `<Button disabled>Save</Button>` or `<Button aria-label="Back"><ArrowLeft /></Button>` fails `pnpm typecheck` today. Vue passes such attributes through to the component's root element at run time (the button really is disabled, the label really is read out), but `strictTemplates` checks every attribute against the component's declared props, and the shadcn-vue copies declare only their own (`variant`, `size`, `class`). The same holds for `<Input type="search" placeholder="…" id="…">`. S0169 did not meet it: its tests mount the components from TypeScript, not from a template. S0170's gallery is the first template that uses them; every screen of M2 will.
Writing the attributes as an object (`v-bind="{ 'aria-label': 'Back' }"`) passes the gate, because the gate does not check that form. That works, but it hides the attributes from the type gate and would spread as a habit.

## Options (cheapest first; PROCESS.md §5)
1. **Declare the common attributes once** in `src/app/vendored-ui.d.ts`, where the copies' `data-*` names already are: every ARIA attribute (Vue's own `AriaAttributes` type) and `id`, `type`, `disabled`, `placeholder` (more names added in review as screens need them). Checked: with these, the gallery passes `vue-tsc` without the object form. A misspelt prop of our own components is still refused; only these names are accepted on any component, and Vue applies them to the root element. Cost: a component that ignores, say, `disabled` would not be refused at type level (tests see it).
2. **Turn on `fallthroughAttributes`** (a Vue compiler option that types a component's attributes from its root element) **and wrap Reka-rooted copies in our own components.** Checked: it fixes the input (its root is `<input>`), not the button, whose root is Reka's `Primitive` (any element), so every button attribute would need a typed wrapper in `components/app` (`IconButton` with a required label, which ADR-0035 already plans, a toggle button with `pressed`, and so on). Strictest, and most code.
3. **Turn off `checkUnknownProps`** (one part of `strictTemplates`), keeping the checks on unknown components, events and directives. Cheapest to do, but a misspelt prop on our own components then becomes a silent HTML attribute. Not recommended.

## Recommendation
Option 1: one declaration beside the existing one keeps the gate checking every other name and needs no wrappers for plain attributes; the ADR-0035 amendment's "only these names" becomes "these names and the listed HTML attributes". The planned `IconButton` (label required) still comes with its slice. Review history: the vendored-types family had one round in S0169 (the `data-*` declaration, no finding).

## Answer (owner, verbatim, with date)
