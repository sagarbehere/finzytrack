<template>
  <div class="pb-6">
    <!-- Recipe type selector (pill tabs) -->
    <div class="mb-2 flex flex-wrap gap-2 sm:space-x-4 sm:gap-0">
      <button
        v-for="rt in recipeTypes"
        :key="rt.id"
        @click="switchRecipeType(rt.id)"
        :class="[
          recipeType === rt.id
            ? 'rounded-md bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300'
            : 'rounded-md px-3 py-2 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
        ]"
      >
        {{ rt.label }}
      </button>
    </div>

    <p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
      Manage dashboard and widget recipe files.
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
          No {{ recipeTypeLabel }} recipes found.
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
                :placeholder="`{\n  &quot;id&quot;: &quot;my-${recipeType === 'dashboards' ? 'dashboard' : 'widget'}&quot;,\n  &quot;title&quot;: &quot;My ${recipeTypeLabel}&quot;\n}`"
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
import { ref, computed, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { RecipesService } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import { resolveRecipeGenerators } from '@/recipes/functions'
import type { JsonDashboardRecipe } from '@/types/recipes'

const { loadUserRecipes, checkIdConflict } = useRecipeLoader()

const props = defineProps<{
  initialRecipeType?: string
}>()

type RecipeTypeId = 'dashboards' | 'widgets'

const recipeTypes = [
  { id: 'dashboards' as const, label: 'Dashboards' },
  { id: 'widgets' as const, label: 'Widgets' },
]

const recipeType = ref<RecipeTypeId>(
  props.initialRecipeType === 'widgets' ? 'widgets' : 'dashboards'
)

const recipeTypeLabel = computed(() => {
  return recipeType.value === 'dashboards' ? 'Dashboard' : 'Widget'
})

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

function wrapWidgetAsDashboard(widget: Record<string, unknown>): JsonDashboardRecipe {
  const id = (widget.id as string) || 'widget'
  return {
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
    let dashboard: JsonDashboardRecipe
    if (recipeType.value === 'widgets') {
      dashboard = wrapWidgetAsDashboard(parsed)
    } else {
      dashboard = parsed as unknown as JsonDashboardRecipe
    }
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
    const paths: string[] = manifest[recipeType.value] ?? []
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

async function switchRecipeType(newType: RecipeTypeId) {
  if (newType === recipeType.value) return
  if (!(await checkDirtyBeforeAction())) return

  recipeType.value = newType
  selectedFile.value = null
  isCreating.value = false
  newFilename.value = ''
  editorContent.value = ''
  originalContent.value = ''
  jsonParseError.value = null
  previewDashboard.value = null
  previewError.value = null
  await loadFileList()
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

  // Check for ID conflicts before saving
  const recipeId = parsed.id as string | undefined
  if (recipeId) {
    const type = recipeType.value === 'dashboards' ? 'dashboard' : 'widget'
    const currentPath = isCreating.value ? undefined : selectedFile.value ?? undefined
    const conflict = checkIdConflict(recipeId, type as 'widget' | 'dashboard', currentPath)
    if (conflict) {
      const kindLabel =
        conflict.kind === 'widget' ? 'a standalone widget recipe' :
        conflict.kind === 'dashboard' ? 'a dashboard recipe' :
        'an inline widget in a dashboard'
      const proceed = await confirmDialog.showConfirm({
        title: 'Recipe ID Conflict',
        message: `The ${type} ID "${recipeId}" is already used by ${kindLabel} in ${conflict.conflictingFile}. Saving will cause one definition to silently override the other, leading to unpredictable behavior.\n\nDo you want to save anyway?`,
        confirmText: 'Save Anyway',
        cancelText: 'Go Back',
        variant: 'warning',
      })
      if (!proceed) return
    }

    // For dashboards, also check inline widget IDs against standalone widgets
    if (type === 'dashboard' && Array.isArray(parsed.widgets)) {
      for (const inlineWidget of parsed.widgets as Array<{ id?: string }>) {
        if (!inlineWidget.id) continue
        const inlineConflict = checkIdConflict(inlineWidget.id, 'widget', undefined)
        if (inlineConflict) {
          const proceed = await confirmDialog.showConfirm({
            title: 'Inline Widget ID Conflict',
            message: `This dashboard defines an inline widget with ID "${inlineWidget.id}" which conflicts with ${inlineConflict.conflictingFile}. The inline definition will silently override the standalone widget recipe.\n\nDo you want to save anyway?`,
            confirmText: 'Save Anyway',
            cancelText: 'Go Back',
            variant: 'warning',
          })
          if (!proceed) return
          break
        }
      }
    }
  }

  isSaving.value = true
  try {
    if (isCreating.value) {
      let filename = newFilename.value.trim()
      if (!filename.endsWith('.json')) filename += '.json'
      const filePath = `${recipeType.value}/${filename}`
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

watch(() => props.initialRecipeType, (newType) => {
  if (newType && recipeTypes.some(rt => rt.id === newType) && newType !== recipeType.value) {
    switchRecipeType(newType as RecipeTypeId)
  }
})

// Ensure the global widget registry is populated so external widget references resolve in previews
loadUserRecipes().then(() => loadFileList())
</script>
