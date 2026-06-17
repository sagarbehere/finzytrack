<template>
  <TransitionRoot appear :show="isOpen" as="template">
    <Dialog as="div" @close="close" class="relative z-[60]">
      <TransitionChild
        as="template"
        enter="duration-200 ease-out" enter-from="opacity-0" enter-to="opacity-100"
        leave="duration-150 ease-in" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/40 dark:bg-black/60" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
            as="template"
            enter="duration-200 ease-out" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="duration-150 ease-in" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="flex h-[85vh] w-full max-w-4xl transform flex-col overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow-xl">
              <!-- Header -->
              <div class="flex items-center justify-between gap-4 border-b border-gray-200 dark:border-white/10 px-4 py-3">
                <DialogTitle class="truncate text-sm font-medium text-gray-900 dark:text-white">
                  {{ displayName }}
                </DialogTitle>
                <div class="flex items-center gap-2">
                  <a
                    :href="url"
                    :download="displayName"
                    class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  >Download</a>
                  <button
                    type="button"
                    @click="close"
                    class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    <XMarkIcon class="h-5 w-5" />
                  </button>
                </div>
              </div>

              <!-- Body -->
              <div class="relative flex-1 overflow-auto bg-gray-50 dark:bg-gray-900/50">
                <img
                  v-if="kind === 'image'"
                  :src="url"
                  :alt="displayName"
                  class="mx-auto max-h-full max-w-full object-contain"
                />
                <iframe
                  v-else-if="kind === 'pdf'"
                  :src="url"
                  class="h-full w-full"
                  :title="displayName"
                ></iframe>
                <div v-else class="flex h-full items-center justify-center p-8 text-center">
                  <p class="text-sm text-gray-500 dark:text-gray-400">
                    This file type can't be previewed inline. Use <strong>Download</strong> to open it.
                  </p>
                </div>

                <!-- Fallback hint when the embedded PDF viewer may be missing
                     (packaged desktop app on Linux / WebKitGTK has no built-in
                     PDF renderer). -->
                <div
                  v-if="kind === 'pdf' && mayLackPdfViewer"
                  class="border-t border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300"
                >
                  If the document above is blank, your system may lack a PDF viewer for the
                  in-app window. Install one and restart Finzytrack — e.g.
                  <code>sudo apt install libwebkit2gtk-4.1-0</code> (Debian/Ubuntu),
                  <code>sudo dnf install webkit2gtk4.1</code> (Fedora), or
                  <code>sudo pacman -S webkit2gtk-4.1</code> (Arch) — or just use
                  <strong>Download</strong>.
                </div>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed, watch, onUnmounted } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { useDocuments } from '@/composables/useDocuments'
import { useDocumentPreview } from '@/composables/useDocumentPreview'

const { isOpen, path, displayName, close } = useDocumentPreview()
const { serveUrl } = useDocuments()

// This modal is rendered *nested* inside whichever drawer/modal opened it, so
// HeadlessUI's stack disables the underlying dialog's Escape + outside-click
// while the preview is up (that's what stops "Esc closes everything"). But a
// nested HeadlessUI dialog also disables its *own* Escape, so we close the
// preview on Escape ourselves. The underlying dialog's window keydown handler
// is inert here (stacked), so this is conflict-free.
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isOpen.value) {
    e.stopPropagation()
    close()
  }
}
watch(isOpen, (open) => {
  if (open) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
}, { immediate: true })
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

const url = computed(() => (path.value ? serveUrl(path.value) : ''))

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'avif'])

const kind = computed<'image' | 'pdf' | 'other'>(() => {
  const ext = (displayName.value.split('.').pop() || '').toLowerCase()
  if (ext === 'pdf') return 'pdf'
  if (IMAGE_EXT.has(ext)) return 'image'
  return 'other'
})

// Heuristic: PyWebView injects `window.pywebview`; on Linux that's WebKitGTK,
// which has no guaranteed built-in PDF viewer. We can't feature-detect a blank
// iframe, so we surface the install hint in that case (the iframe still renders
// where supported, e.g. macOS WKWebView).
const mayLackPdfViewer = computed(() =>
  typeof window !== 'undefined'
  && !!(window as any).pywebview
  && /linux/i.test(navigator.userAgent || ''),
)
</script>
