<template>
  <div ref="editorContainer" class="monaco-editor-container" :style="{ height: height }"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import * as monaco from 'monaco-editor'

interface Props {
  modelValue: string
  language?: string
  readonly?: boolean
  options?: monaco.editor.IStandaloneEditorConstructionOptions
  height?: string
}

interface Emits {
  (e: 'update:modelValue', value: string): void
  (e: 'change', value: string): void
}

const props = withDefaults(defineProps<Props>(), {
  language: 'plaintext',
  readonly: false,
  options: () => ({}),
  height: '500px',
})

const emit = defineEmits<Emits>()

const editorContainer = ref<HTMLElement | null>(null)
let editor: monaco.editor.IStandaloneCodeEditor | null = null
let ignoreChangeEvent = false

// Detect dark mode
const isDarkMode = computed(() => {
  if (typeof window === 'undefined') return false
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
})

// Get appropriate theme
const editorTheme = computed(() => {
  return isDarkMode.value ? 'vs-dark' : 'vs'
})

onMounted(() => {
  if (!editorContainer.value) return

  // Create editor instance
  editor = monaco.editor.create(editorContainer.value, {
    value: props.modelValue,
    language: props.language,
    readOnly: props.readonly,
    theme: editorTheme.value,
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 14,
    lineNumbers: 'on',
    scrollBeyondLastLine: false,
    tabSize: 2,
    wordWrap: 'on',
    renderWhitespace: 'selection',
    ...props.options,
  })

  // Listen for content changes
  editor.onDidChangeModelContent(() => {
    if (!editor || ignoreChangeEvent) return

    const value = editor.getValue()
    emit('update:modelValue', value)
    emit('change', value)
  })

  // Listen for dark mode changes
  const darkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  const handleThemeChange = (e: MediaQueryListEvent) => {
    if (editor) {
      monaco.editor.setTheme(e.matches ? 'vs-dark' : 'vs')
    }
  }

  darkModeMediaQuery.addEventListener('change', handleThemeChange)

  // Store cleanup function
  onBeforeUnmount(() => {
    darkModeMediaQuery.removeEventListener('change', handleThemeChange)
  })
})

onBeforeUnmount(() => {
  editor?.dispose()
})

// Watch for external value changes
watch(
  () => props.modelValue,
  (newValue) => {
    if (!editor) return

    const currentValue = editor.getValue()
    if (newValue !== currentValue) {
      ignoreChangeEvent = true
      editor.setValue(newValue)
      ignoreChangeEvent = false
    }
  }
)

// Watch for readonly changes
watch(
  () => props.readonly,
  (newReadonly) => {
    if (!editor) return
    editor.updateOptions({ readOnly: newReadonly })
  }
)

// Watch for language changes
watch(
  () => props.language,
  (newLanguage) => {
    if (!editor) return
    const model = editor.getModel()
    if (model) {
      monaco.editor.setModelLanguage(model, newLanguage)
    }
  }
)

// Expose methods for parent component
defineExpose({
  getEditor: () => editor,
  focus: () => editor?.focus(),
  getValue: () => editor?.getValue() || '',
  setValue: (value: string) => {
    if (editor) {
      ignoreChangeEvent = true
      editor.setValue(value)
      ignoreChangeEvent = false
    }
  },
  revealLineInCenter: (lineNumber: number) => {
    editor?.revealLineInCenter(lineNumber)
  },
  setPosition: (position: monaco.IPosition) => {
    editor?.setPosition(position)
  },
})
</script>

<style scoped>
.monaco-editor-container {
  width: 100%;
  border: 1px solid #e5e7eb;
  border-radius: 0.375rem;
  overflow: hidden;
}
</style>
