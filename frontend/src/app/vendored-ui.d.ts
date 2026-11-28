// The data-* attributes the vendored shadcn-vue copies set (src/components/ui), declared so `strictTemplates`
// accepts them without a patch to the copies (ADR-0035, amendment of 2026-11-28). Only these names; Vue camelizes
// an attribute passed to a component, so the component form is camelCase.
export {}

declare module 'vue' {
  interface AllowedComponentProps {
    dataSlot?: string
    dataVariant?: unknown
    dataSize?: unknown
  }
  interface HTMLAttributes {
    'data-slot'?: string
  }
}
