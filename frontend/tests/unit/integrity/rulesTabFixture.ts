// A fixture tab for the L9.M-1 test (guards.test.ts): an editor that becomes dirty when typed into and registers the
// unsaved guard itself, as the legacy RulesTab did, so the test catches a guard that dies with the tab's component.
import { defineComponent, h, ref, type Component, type Ref } from 'vue'

export function rulesTabFixture(useUnsavedGuard: (dirty: Ref<boolean>) => unknown): Component {
  return defineComponent({
    setup() {
      const text = ref('rule: groceries')
      const saved = 'rule: groceries'
      const dirty = ref(false)
      useUnsavedGuard(dirty)
      return () =>
        h('textarea', {
          'aria-label': 'Rule',
          value: text.value,
          onInput: (event: Event) => {
            text.value = (event.target as HTMLTextAreaElement).value
            dirty.value = text.value !== saved
          },
        })
    },
  })
}
