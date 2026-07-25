<template>
  <div class="pb-6">
    <!-- Color theme picker -->
    <section class="mb-8">
      <h3 class="text-sm font-semibold text-gray-900 dark:text-white">Color theme</h3>
      <p class="mt-1 mb-3 text-sm text-gray-500 dark:text-gray-400">
        The color palette used across all dashboard charts, KPIs, and budget bars. Changes apply immediately.
      </p>

      <div v-if="themesLoading" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 py-2">
        <div class="animate-spin h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
        Loading themes...
      </div>

      <div v-else-if="themes.length === 0" class="text-sm text-gray-500 dark:text-gray-400">
        No themes found.
      </div>

      <div v-else class="max-w-sm">
        <Listbox as="div" :model-value="activeThemeId" :disabled="savingTheme" @update:model-value="selectTheme">
          <div class="relative">
            <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-wait disabled:opacity-70 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
              <span class="col-start-1 row-start-1 truncate pr-6">{{ selectedThemeName }}</span>
              <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
            </ListboxButton>
            <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
              <ListboxOptions class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                <ListboxOption v-for="theme in themes" :key="theme.id" :value="theme.id" as="template" v-slot="{ active, selected }">
                  <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                    <span :class="[selected ? 'font-semibold' : 'font-normal', 'flex items-center gap-2']">
                      <span class="truncate">{{ theme.name }}</span>
                      <span
                        v-if="theme.id === defaultThemeId"
                        :class="[active ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500 dark:bg-white/10 dark:text-gray-300', 'rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide']"
                      >Default</span>
                    </span>
                    <span
                      v-if="theme.description"
                      :class="[active ? 'text-indigo-100' : 'text-gray-500 dark:text-gray-400', 'mt-0.5 block text-xs font-normal']"
                    >{{ stripDefaultSuffix(theme.description) }}</span>
                    <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute top-2.5 right-0 flex items-center pr-4']">
                      <CheckIcon class="size-5" aria-hidden="true" />
                    </span>
                  </li>
                </ListboxOption>
              </ListboxOptions>
            </transition>
          </div>
        </Listbox>
        <p v-if="selectedThemeDescription" class="mt-2 text-xs text-gray-500 dark:text-gray-400">
          {{ selectedThemeDescription }}
        </p>
      </div>

      <p v-if="themeError" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ themeError }}</p>
    </section>

    <div class="mb-6 border-t border-gray-200 dark:border-white/10"></div>

    <p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
      Manage dashboard recipe files.
      <a href="https://docs.finzytrack.com/reference/dashboard-recipes/" target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300">View documentation</a>.
      To rename a recipe file, create a new one and delete the old.
    </p>

    <!-- Loading state -->
    <div v-if="isLoading" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 py-8 justify-center">
      <div class="animate-spin h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full"></div>
      Loading recipes...
    </div>

    <template v-else>
      <!-- Empty state -->
      <div
        v-if="files.length === 0 && !isCreating"
        class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded-lg p-4 text-sm text-yellow-800 dark:text-yellow-200 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
      >
        <span>
          No dashboard recipes found.
          <a href="https://docs.finzytrack.com/reference/dashboard-recipes/" target="_blank" rel="noopener noreferrer" class="underline underline-offset-2 hover:text-yellow-900 dark:hover:text-yellow-100">
            Learn how to create recipes
          </a>.
        </span>
        <button
          @click="startCreate"
          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        >
          New Recipe
        </button>
      </div>

      <!-- Two-column layout: file list + editor/preview -->
      <div v-else class="flex flex-col gap-4 md:flex-row">
        <!-- File list -->
        <div class="w-full md:w-64 md:shrink-0 rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col max-h-48 md:max-h-none">
          <div class="border-b border-gray-200 px-3 py-2.5 dark:border-white/10 flex items-center justify-between">
            <span class="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">Files</span>
            <button
              @click="startCreate"
              class="text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
            >
              + New
            </button>
          </div>
          <ul class="flex-1 overflow-y-auto py-1">
            <li
              v-for="file in files"
              :key="file.path"
              @click="selectFile(file.path)"
              :class="[
                'px-3 py-2 text-sm cursor-pointer',
                selectedFile === file.path
                  ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300'
                  : 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-white/5',
              ]"
            >
              <span class="truncate block">{{ file.displayName }}</span>
            </li>
          </ul>
        </div>

        <!-- Editor + Preview (right, stacked or side-by-side) -->
        <div class="flex-1 flex gap-4" :class="layoutVertical ? 'flex-col' : 'flex-row'">
          <!-- Editor card -->
          <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 flex flex-col" :class="layoutVertical ? '' : 'flex-1 min-w-0'">
            <!-- Editor header -->
            <div class="flex flex-col gap-2 border-b border-gray-200 px-4 py-3 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
              <div class="flex items-center gap-2 min-w-0">
                <span v-if="isCreating" class="text-sm font-medium text-gray-900 dark:text-white">New Recipe</span>
                <span v-else-if="selectedFile" class="text-sm font-medium text-gray-900 dark:text-white truncate">{{ selectedDisplayName }}</span>
                <span v-else class="text-sm text-gray-500 dark:text-gray-400">Select a file to edit</span>
                <span v-if="isDirty" class="text-xs text-amber-600 dark:text-amber-400">Unsaved changes</span>
              </div>
              <div v-if="selectedFile || isCreating" class="flex items-center gap-2 shrink-0 self-end sm:self-auto">
                <button
                  v-if="selectedFile && !isCreating"
                  @click="handleDelete"
                  :disabled="isDeleting"
                  class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-red-600 shadow-xs inset-ring inset-ring-gray-300 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed shrink-0 dark:bg-white/10 dark:text-red-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-red-900/20"
                >
                  {{ isDeleting ? 'Deleting...' : 'Delete' }}
                </button>
                <button
                  @click="handleCancel"
                  class="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  Cancel
                </button>
                <button
                  @click="handleSave"
                  :disabled="!canSave || isSaving"
                  class="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
                >
                  {{ isSaving ? 'Saving...' : 'Save' }}
                </button>
              </div>
            </div>

            <!-- New file: filename input -->
            <div v-if="isCreating" class="border-b border-gray-200 px-4 py-3 dark:border-white/10">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Filename</label>
              <input
                v-model="newFilename"
                type="text"
                placeholder="my-recipe.json"
                class="block w-full rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
              />
            </div>

            <!-- JSON parse error -->
            <div v-if="jsonParseError" class="mx-4 mt-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700 p-3 text-sm text-red-700 dark:text-red-300">
              {{ jsonParseError }}
            </div>

            <!-- Textarea (shorter to leave room for preview) -->
            <div class="p-4">
              <textarea
                v-if="selectedFile || isCreating"
                v-model="editorContent"
                spellcheck="false"
                class="w-full font-mono text-sm rounded-md bg-white px-3 py-2 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 resize-y"
                style="min-height: 200px; height: 200px;"
                placeholder='{
  "schemaVersion": 2,
  "id": "my-dashboard",
  "title": "My Dashboard"
}'
              />
              <div v-else class="flex items-center justify-center py-12 text-sm text-gray-400 dark:text-gray-500">
                Select a file from the list or create a new recipe
              </div>
            </div>
          </div>

          <!-- Preview card -->
          <div
            v-if="selectedFile || isCreating"
            class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 overflow-auto"
            :class="layoutVertical ? '' : 'flex-1 min-w-0'"
          >
            <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
              <span class="text-sm font-medium text-gray-900 dark:text-white">Preview</span>
              <div class="flex items-center gap-2">
                <span v-if="previewError" class="text-xs text-red-600 dark:text-red-400">{{ previewError }}</span>
                <button
                  @click="refreshPreview"
                  :disabled="!editorContent.trim()"
                  class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed shrink-0 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                >
                  Refresh Preview
                </button>
                <button
                  @click="toggleLayout"
                  class="rounded-md bg-white p-1.5 text-gray-500 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 hover:text-gray-700 shrink-0 dark:bg-white/10 dark:text-gray-400 dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 dark:hover:text-gray-200"
                  :title="layoutVertical ? 'Switch to side-by-side layout' : 'Switch to stacked layout'"
                >
                  <!-- Vertical (stacked) icon: bars stacked -->
                  <svg v-if="layoutVertical" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 4.5v15m6-15v15M4.5 19.5h15a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5h-15A1.5 1.5 0 003 6v12a1.5 1.5 0 001.5 1.5z" />
                  </svg>
                  <!-- Horizontal (side-by-side) icon: bars side by side -->
                  <svg v-else class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 9h15m-15 6h15M4.5 4.5h15a1.5 1.5 0 011.5 1.5v12a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18V6a1.5 1.5 0 011.5-1.5z" />
                  </svg>
                </button>
              </div>
            </div>
            <div class="p-4">
              <div v-if="!previewDashboard" class="flex items-center justify-center py-12 text-sm text-gray-400 dark:text-gray-500">
                Click "Refresh Preview" to render the recipe
              </div>
              <RecipeDashboard v-else :key="previewKey" :dashboard="previewDashboard" />
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Confirm dialog -->
    <ConfirmDialog
      :is-open="confirmDialog.isOpen.value"
      :title="confirmDialog.dialogOptions.value.title"
      :message="confirmDialog.dialogOptions.value.message"
      :confirm-text="confirmDialog.dialogOptions.value.confirmText"
      :cancel-text="confirmDialog.dialogOptions.value.cancelText"
      :variant="confirmDialog.dialogOptions.value.variant"
      @confirm="confirmDialog.handleConfirm"
      @cancel="confirmDialog.handleCancel"
      @close="confirmDialog.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Listbox, ListboxButton, ListboxOptions, ListboxOption } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { RecipesService, DashboardThemesService } from '@/services/generated-api'
import type { DashboardThemeSummary } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import { useDashboardTheme } from '@/composables/useDashboardTheme'
import { useConfig } from '@/composables/useConfig'
import { patchConfig } from '@/composables/useConfigPatch'
import { resolveRecipeGenerators } from '@/recipes/functions'
import type { JsonDashboardRecipe } from '@/types/recipes'

const { loadUserRecipes, checkIdConflict } = useRecipeLoader()

// --- Color theme picker ---

const { loadTheme, defaultThemeId } = useDashboardTheme()
const { updateConfig } = useConfig()

// The default badge conveys "the default", so drop that trailing phrase from the
// description text to avoid saying it twice.
function stripDefaultSuffix(description: string): string {
  return description.replace(/\s*The default\.?\s*$/i, '')
}

const themes = ref<DashboardThemeSummary[]>([])
const activeThemeId = ref<string | null>(null)
const themesLoading = ref(false)
const savingTheme = ref(false)
const themeError = ref<string | null>(null)

const selectedTheme = computed(() => themes.value.find((t) => t.id === activeThemeId.value))
const selectedThemeName = computed(() => selectedTheme.value?.name ?? 'Select theme')
const selectedThemeDescription = computed(() => selectedTheme.value?.description ?? '')

async function loadThemes() {
  themesLoading.value = true
  themeError.value = null
  try {
    const resp = await DashboardThemesService.listDashboardThemesApiDashboardThemesGet()
    themes.value = resp.data?.themes ?? []
    activeThemeId.value = resp.data?.active ?? null
  } catch (e) {
    errorHandler.display(e)
    themes.value = []
  } finally {
    themesLoading.value = false
  }
}

async function selectTheme(id: string) {
  if (id === activeThemeId.value || savingTheme.value) return
  const previous = activeThemeId.value
  savingTheme.value = true
  themeError.value = null
  activeThemeId.value = id // optimistic
  try {
    const result = await patchConfig({ active_dashboard_theme: id })
    updateConfig(result.config)
    await loadTheme() // re-fetch the active theme so charts recolor live
  } catch (e: any) {
    activeThemeId.value = previous // revert on failure
    themeError.value = e?.message ?? 'Failed to switch theme'
  } finally {
    savingTheme.value = false
  }
}

// --- File list state ---

interface FileEntry {
  path: string
  displayName: string
}

const files = ref<FileEntry[]>([])
const selectedFile = ref<string | null>(null)
const editorContent = ref('')
const originalContent = ref('')
const isDirty = computed(() => editorContent.value !== originalContent.value)

const isLoading = ref(false)
const isSaving = ref(false)
const isDeleting = ref(false)

const isCreating = ref(false)
const newFilename = ref('')
const jsonParseError = ref<string | null>(null)

// --- Layout state (persisted) ---

const layoutVertical = ref(getStorageAdapter().get<string>(STORAGE_KEYS.DASHBOARDS_TAB_LAYOUT) !== 'horizontal')

function toggleLayout() {
  layoutVertical.value = !layoutVertical.value
  getStorageAdapter().set(STORAGE_KEYS.DASHBOARDS_TAB_LAYOUT, layoutVertical.value ? 'vertical' : 'horizontal')
}

// --- Preview state ---

const previewDashboard = ref<JsonDashboardRecipe | null>(null)
const previewError = ref<string | null>(null)
const previewKey = ref(0)

const selectedDisplayName = computed(() => {
  if (!selectedFile.value) return ''
  const parts = selectedFile.value.split('/')
  return parts[parts.length - 1]
})

const canSave = computed(() => {
  if (isCreating.value) return newFilename.value.trim().length > 0 && editorContent.value.trim().length > 0
  return isDirty.value
})

const confirmDialog = useConfirmDialog()

// --- Preview ---

// Wrap a single inline widget in a throwaway one-widget dashboard so authors can
// preview a widget in isolation (refactored-dashboard-recipes.md §4.10). This is
// a preview-only affordance — there is no standalone widget *file*.
function wrapWidgetAsDashboard(widget: Record<string, unknown>): JsonDashboardRecipe {
  const id = (widget.id as string) || 'widget'
  return {
    schemaVersion: 2,
    id: `__preview__${id}`,
    title: (widget.title as string) || 'Preview',
    parameters: (widget.parameters as JsonDashboardRecipe['parameters']) || [],
    layout: {
      columns: 6,
      gap: '1.5rem',
      rowHeight: '200px',
      widgets: [{ widgetId: id, gridArea: '1 / 1 / 4 / 7' }],
    },
    widgets: [widget as any],
  }
}

function refreshPreview() {
  previewError.value = null
  previewDashboard.value = null

  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(editorContent.value)
  } catch (e) {
    previewError.value = `Invalid JSON: ${(e as Error).message}`
    return
  }

  try {
    // Auto-detect: a pasted single widget (has a visualization but no dashboard
    // layout) is wrapped for preview; a full dashboard renders as-is.
    const isSingleWidget = !parsed.layout && !!parsed.visualization
    const dashboard = isSingleWidget
      ? wrapWidgetAsDashboard(parsed)
      : (parsed as unknown as JsonDashboardRecipe)
    const resolved = resolveRecipeGenerators(dashboard)
    previewDashboard.value = resolved
    previewKey.value++
  } catch (e) {
    previewError.value = `Preview error: ${(e as Error).message}`
  }
}

// --- Data loading ---

async function loadFileList() {
  isLoading.value = true
  try {
    const manifest = await RecipesService.getManifestApiRecipesManifestJsonGet()
    const paths: string[] = manifest.dashboards ?? []
    files.value = paths.map((p: string) => ({
      path: p,
      displayName: p.split('/').pop() || p,
    }))
  } catch (e) {
    errorHandler.display(e)
    files.value = []
  } finally {
    isLoading.value = false
  }
}

async function loadFileContent(filePath: string) {
  try {
    const resp = await RecipesService.getRecipeRaw(filePath)
    const content = resp.data!.content
    editorContent.value = content
    originalContent.value = content
    jsonParseError.value = null
    // Auto-refresh preview when loading a file
    refreshPreview()
  } catch (e) {
    errorHandler.display(e)
  }
}

// --- Actions ---

async function checkDirtyBeforeAction(): Promise<boolean> {
  if (!isDirty.value) return true
  return await confirmDialog.showConfirm({
    title: 'Unsaved Changes',
    message: 'You have unsaved changes. Discard them?',
    confirmText: 'Discard',
    cancelText: 'Cancel',
    variant: 'warning',
  })
}

async function selectFile(filePath: string) {
  if (filePath === selectedFile.value && !isCreating.value) return
  if (!(await checkDirtyBeforeAction())) return

  isCreating.value = false
  newFilename.value = ''
  jsonParseError.value = null
  previewDashboard.value = null
  previewError.value = null
  selectedFile.value = filePath
  await loadFileContent(filePath)
}

async function startCreate() {
  if (!(await checkDirtyBeforeAction())) return

  isCreating.value = true
  selectedFile.value = null
  newFilename.value = ''
  editorContent.value = ''
  originalContent.value = ''
  jsonParseError.value = null
  previewDashboard.value = null
  previewError.value = null
}

function handleCancel() {
  if (isCreating.value) {
    isCreating.value = false
    newFilename.value = ''
    editorContent.value = ''
    originalContent.value = ''
  } else {
    editorContent.value = originalContent.value
  }
  jsonParseError.value = null
  previewDashboard.value = null
  previewError.value = null
}

async function handleSave() {
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(editorContent.value)
  } catch (e) {
    jsonParseError.value = `Invalid JSON: ${(e as Error).message}`
    return
  }
  jsonParseError.value = null

  // Check for dashboard ID conflicts before saving (dashboard is the only recipe type)
  const recipeId = parsed.id as string | undefined
  if (recipeId) {
    const currentPath = isCreating.value ? undefined : selectedFile.value ?? undefined
    const conflict = checkIdConflict(recipeId, currentPath)
    if (conflict) {
      const proceed = await confirmDialog.showConfirm({
        title: 'Recipe ID Conflict',
        message: `The dashboard ID "${recipeId}" is already used by a dashboard recipe in ${conflict.conflictingFile}. Saving will cause one definition to silently override the other, leading to unpredictable behavior.\n\nDo you want to save anyway?`,
        confirmText: 'Save Anyway',
        cancelText: 'Go Back',
        variant: 'warning',
      })
      if (!proceed) return
    }
  }

  isSaving.value = true
  try {
    if (isCreating.value) {
      let filename = newFilename.value.trim()
      if (!filename.endsWith('.json')) filename += '.json'
      const filePath = `dashboards/${filename}`
      await RecipesService.writeRecipeFileApiRecipesFilePathPut(filePath, { content: parsed })
      isCreating.value = false
      await loadFileList()
      selectedFile.value = filePath
      originalContent.value = editorContent.value
    } else if (selectedFile.value) {
      await RecipesService.writeRecipeFileApiRecipesFilePathPut(selectedFile.value, { content: parsed })
      originalContent.value = editorContent.value
      await loadFileList()
    }
  } catch (e) {
    errorHandler.display(e)
  } finally {
    isSaving.value = false
  }
}

async function handleDelete() {
  if (!selectedFile.value) return

  const confirmed = await confirmDialog.showConfirm({
    title: 'Delete Recipe',
    message: `Delete "${selectedDisplayName.value}"? A timestamped backup is kept in the backup directory; you can restore it from there if needed.`,
    confirmText: 'Delete',
    cancelText: 'Cancel',
    variant: 'danger',
  })
  if (!confirmed) return

  isDeleting.value = true
  try {
    await RecipesService.deleteRecipeFileApiRecipesFilePathDelete(selectedFile.value)
    selectedFile.value = null
    editorContent.value = ''
    originalContent.value = ''
    jsonParseError.value = null
    previewDashboard.value = null
    previewError.value = null
    await loadFileList()
  } catch (e) {
    errorHandler.display(e)
  } finally {
    isDeleting.value = false
  }
}

// --- Navigation guard ---

onBeforeRouteLeave(async (_to, _from, next) => {
  if (isDirty.value) {
    const ok = await confirmDialog.showConfirm({
      title: 'Unsaved Changes',
      message: 'You have unsaved changes. Leave without saving?',
      confirmText: 'Leave',
      cancelText: 'Stay',
      variant: 'warning',
    })
    next(ok)
  } else {
    next()
  }
})

// --- Lifecycle ---

// Load user recipes (for id-conflict detection) then the dashboard file list.
loadUserRecipes().then(() => loadFileList())

// Load the theme picker options (independent of the recipe file list).
loadThemes()
</script>
