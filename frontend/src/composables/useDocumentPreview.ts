/**
 * App-level document preview state.
 *
 * The packaged desktop app is a single PyWebView window with no browser tabs,
 * and `window.open` is unreliable there — so documents are previewed in an
 * in-app modal rather than a new tab. This module-level singleton lets any
 * component (transaction drawer, account drawer, orphan modal) trigger the one
 * `DocumentPreviewModal` mounted in the app shell.
 */
import { ref } from 'vue'

const isOpen = ref(false)
const path = ref('')
const displayName = ref('')

export function useDocumentPreview() {
  function openPreview(documentPath: string, name?: string) {
    path.value = documentPath
    displayName.value = name || documentPath.split('/').pop() || documentPath
    isOpen.value = true
  }

  function close() {
    isOpen.value = false
  }

  return { isOpen, path, displayName, openPreview, close }
}
