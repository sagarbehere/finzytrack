<template>
  <div class="h-full flex flex-col">
    <!-- Tab bar -->
    <DashboardTabs
      :tabs="tabs"
      :activeTabId="activeTabId"
      @select="setActiveTab"
      @remove="removeTab"
      @add="showPicker = true"
    />

    <!-- Dashboard content -->
    <div class="flex-1 overflow-auto p-6">
      <RecipeDashboard
        v-if="activeDashboard"
        :dashboard="activeDashboard"
        :key="activeTabId ?? undefined"
        db-type="sqlite"
      />
      <div v-else class="flex flex-col items-center justify-center h-full text-gray-500 dark:text-gray-400">
        <div class="text-lg mb-2">No dashboard selected</div>
        <button
          @click="showPicker = true"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          Click here to add one
        </button>
      </div>
    </div>

    <!-- Picker modal -->
    <DashboardPicker
      :isOpen="showPicker"
      :availableDashboards="allDashboards"
      :selectedIds="selectedDashboardIds"
      @close="showPicker = false"
      @select="handleAddDashboard"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import DashboardTabs from '@/components/dashboard/DashboardTabs.vue'
import DashboardPicker from '@/components/dashboard/DashboardPicker.vue'
import { useRecipeLoader } from '@/composables/useRecipeLoader'
import { useDashboardTabs } from '@/composables/useDashboardTabs'

const { loadUserRecipes, getAllDashboardIds, getDashboard } = useRecipeLoader()
const { tabs, activeTabId, addTab, removeTab, setActiveTab, loadTabs, activeDashboard } = useDashboardTabs()

const showPicker = ref(false)

// Computed: all available dashboards with metadata
const allDashboards = computed(() => {
  return getAllDashboardIds().map((id) => {
    const d = getDashboard(id)
    return {
      id,
      title: d?.title || id,
      description: d?.description,
    }
  })
})

// Computed: IDs of dashboards already in tabs
const selectedDashboardIds = computed(() => tabs.value.map((t) => t.id))

// Add dashboard handler
function handleAddDashboard(id: string) {
  const dashboard = getDashboard(id)
  if (dashboard) {
    addTab(id, dashboard.title)
    setActiveTab(id)
  }
  showPicker.value = false
}

// Initialize on mount
onMounted(async () => {
  await loadUserRecipes()
  await loadTabs()
})
</script>
