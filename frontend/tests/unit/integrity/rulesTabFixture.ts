// A fixture tab for the L9.M-1 test (guards.test.ts): an editor with an unsaved draft that registers the unsaved
// guard itself, as the legacy RulesTab did, so the test catches a guard that dies with the tab's component.
import { defineComponent, h, ref, type Component, type Ref } from 'vue'

export function rulesTabFixture(useUnsavedGuard: (dirty: Ref<boolean>) => unknown): Component {
  return defineComponent({
    setup() {
      const text = ref('rule: groceries')
      useUnsavedGuard(ref(true))
      return () => h('textarea', { 'aria-label': 'Rule', value: text.value })
    },
  })
}
