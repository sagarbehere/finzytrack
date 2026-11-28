# Patches to the vendored copies

The files in this folder are what the shadcn-vue CLI writes (style `new-york-v4`, shadcn-vue 2.9.0: the registry items
of the `v2.9.0` tag), byte for byte, except the patches below. A patch exists only to make a copy pass our type gate
and keeps its behaviour; re-apply it whenever the component is copied again (ADR-0035, amendment of 2026-11-28).
How a copy is made (S0169): each file's `content` from the registry item
`apps/v4/public/r/styles/new-york-v4/<name>.json` in `unovue/shadcn-vue` at the release tag, written to
`<name>/<file>` here unchanged. The CLI writes the same bytes for these items (its only transform rewrites `@/`
imports, which they do not have); it is not configured here, since it needs a `@/` path alias, which our layer map
refuses. Prettier skips this folder, and ESLint turns off `vue/multi-word-component-names` and `vue/require-default-prop` here
only (`tests/unit/gates.test.ts`).

## input/Input.vue

`exactOptionalPropertyTypes` refuses an option that is present with the value `undefined`; VueUse's `useVModel`
options do not allow it. The default is passed only when the caller gave one, which is what an absent default means.

```diff
 const modelValue = useVModel(props, "modelValue", emits, {
   passive: true,
-  defaultValue: props.defaultValue,
+  ...(props.defaultValue === undefined ? {} : { defaultValue: props.defaultValue }),
 })
```

## checkbox/Checkbox.vue (S0170)

The same `exactOptionalPropertyTypes` rule: the forwarded props carry optional values that may be `undefined`, which
`CheckboxRoot`'s declared props do not allow. The object is bound unchanged; only its type is narrowed (a template
`as`), so the emitted handlers and every prop still reach the root.

```diff
   <CheckboxRoot
     v-slot="slotProps"
     data-slot="checkbox"
-    v-bind="forwarded"
+    v-bind="forwarded as CheckboxRootProps"
```
