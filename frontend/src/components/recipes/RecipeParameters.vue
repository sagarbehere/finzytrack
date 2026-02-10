<template>
  <div class="flex flex-wrap items-center gap-4">
    <div
      v-for="param in parameters"
      :key="param.name"
      class="flex items-center gap-2"
    >
      <label
        :for="`param-${param.name}`"
        class="text-sm font-medium text-gray-700 dark:text-gray-300"
      >
        {{ param.label }}
      </label>

      <!-- Select input -->
      <select
        v-if="param.type === 'select'"
        :id="`param-${param.name}`"
        :value="modelValue[param.name]"
        @change="updateParam(param.name, ($event.target as HTMLSelectElement).value)"
        class="block rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white"
      >
        <option
          v-for="option in getOptions(param)"
          :key="String(option.value)"
          :value="option.value"
        >
          {{ option.label }}
        </option>
      </select>

      <!-- Number input -->
      <input
        v-else-if="param.type === 'number'"
        :id="`param-${param.name}`"
        type="number"
        :value="modelValue[param.name]"
        :min="param.min"
        :max="param.max"
        @input="updateParam(param.name, Number(($event.target as HTMLInputElement).value))"
        class="block w-24 rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white"
      />

      <!-- Date input -->
      <input
        v-else-if="param.type === 'date'"
        :id="`param-${param.name}`"
        type="date"
        :value="modelValue[param.name]"
        @input="updateParam(param.name, ($event.target as HTMLInputElement).value)"
        class="block rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { RecipeParameter, RecipeParameterOption } from '@/types/recipes'
import { useCommodities } from '@/composables/useCommodities'

interface Props {
  parameters: RecipeParameter[]
  modelValue: Record<string, string | number>
}

interface Emits {
  (e: 'update:modelValue', value: Record<string, string | number>): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const { commodityDetails, fetchCommodities } = useCommodities()
const dynamicCurrencyOptions = ref<RecipeParameterOption[]>([])

function getOptions(param: RecipeParameter): RecipeParameterOption[] {
  if (param.optionsFrom === 'currencies') {
    return dynamicCurrencyOptions.value
  }
  return param.options || []
}

function coerceValue(value: string | number): string | number {
  if (typeof value === 'number') return value
  const num = Number(value)
  return !isNaN(num) && String(num) === value ? num : value
}

function updateParam(name: string, value: string | number) {
  emit('update:modelValue', {
    ...props.modelValue,
    [name]: coerceValue(value),
  })
}

onMounted(async () => {
  const hasDynamicCurrencies = props.parameters.some((p) => p.optionsFrom === 'currencies')
  if (hasDynamicCurrencies) {
    await fetchCommodities()
    dynamicCurrencyOptions.value = commodityDetails.value
      .filter((c) => c.type === 'Currency' || c.type === null || c.type === undefined)
      .map((c) => ({ value: c.code, label: c.code }))
  }
})
</script>
