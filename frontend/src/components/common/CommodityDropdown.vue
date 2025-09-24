<template>
  <Combobox as="div" v-model="selectedCommodity" @update:modelValue="handleCommoditySelection">
    <ComboboxLabel 
      v-if="label" 
      class="block text-sm/6 font-medium text-gray-900 dark:text-white"
    >
      {{ label }}
    </ComboboxLabel>
    <div :class="['relative', label ? 'mt-2' : '']">
      <ComboboxInput 
        :class="[
          'block w-full rounded-md bg-white py-1.5 pr-12 pl-3 text-base text-gray-900',
          'border border-gray-300 placeholder:text-gray-400',
          'focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500',
          'sm:text-sm dark:bg-white/5 dark:text-white dark:border-gray-600',
          'dark:placeholder:text-gray-500 dark:focus:ring-indigo-500 dark:focus:border-indigo-500',
          'disabled:bg-gray-100 disabled:cursor-not-allowed dark:disabled:bg-gray-700',
          customClass
        ]"
        :disabled="isLoading || disabled"
        :placeholder="placeholder"
        @change="query = $event.target.value" 
        @blur="query = ''" 
        :display-value="(commodity: unknown) => (typeof commodity === 'string' ? commodity : '')"
      />
      <ComboboxButton 
        class="absolute inset-y-0 right-0 flex items-center rounded-r-md px-2 focus:outline-hidden"
        :disabled="isLoading || disabled"
      >
        <ChevronDownIcon 
          v-if="!isLoading" 
          class="size-5 text-gray-400" 
          aria-hidden="true" 
        />
        <div 
          v-else 
          class="size-4 animate-spin rounded-full border-2 border-gray-300 border-t-indigo-600"
        />
      </ComboboxButton>

      <transition 
        leave-active-class="transition ease-in duration-100" 
        leave-from-class="" 
        leave-to-class="opacity-0"
      >
        <ComboboxOptions 
          v-if="(filteredCommodities.length > 0 || query.length > 0) && !isLoading" 
          class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
        >
          <!-- Custom query option - allows user to type new commodity code -->
          <ComboboxOption 
            v-if="queryCommodity && allowCustom" 
            :value="queryCommodity" 
            as="template" 
            v-slot="{ active }"
          >
            <li :class="[
              'relative cursor-default px-3 py-2 select-none',
              active 
                ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' 
                : 'text-gray-900 dark:text-white'
            ]">
              <span class="block truncate">
                {{ query }}
                <span class="text-sm opacity-75 ml-2">(custom)</span>
              </span>
            </li>
          </ComboboxOption>

          <!-- Filtered commodity options -->
          <ComboboxOption 
            v-for="commodityCode in filteredCommodities" 
            :key="commodityCode" 
            :value="commodityCode" 
            as="template" 
            v-slot="{ active }"
          >
            <li :class="[
              'relative cursor-default px-3 py-2 select-none',
              active 
                ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' 
                : 'text-gray-900 dark:text-white'
            ]">
              <div class="flex items-center">
                <span class="block truncate font-medium">
                  {{ commodityCode }}
                </span>
                <span 
                  v-if="getCommodityInfo(commodityCode)" 
                  class="ml-2 text-sm opacity-75 truncate"
                >
                  {{ getCommodityInfo(commodityCode) }}
                </span>
              </div>
            </li>
          </ComboboxOption>

          <!-- No results message -->
          <div 
            v-if="filteredCommodities.length === 0 && query.length > 0 && !allowCustom"
            class="px-3 py-2 text-gray-500 dark:text-gray-400 text-sm"
          >
            No commodities found matching "{{ query }}"
          </div>
        </ComboboxOptions>
      </transition>
    </div>
  </Combobox>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxLabel,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/vue'
import { useCommodities } from '@/composables/useCommodities'

interface Props {
  modelValue?: string
  label?: string
  placeholder?: string
  disabled?: boolean
  customClass?: string
  allowCustom?: boolean
  // Filtering options
  includePattern?: string | RegExp
  excludePattern?: string | RegExp
  commodityTypes?: string[] // e.g., ['Currency', 'Stock']
  showDetails?: boolean // Show commodity name/type in dropdown
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  placeholder: 'Search commodities...',
  disabled: false,
  customClass: '',
  allowCustom: false,
  showDetails: true
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { commodityCodes, commodityDetails, isLoading, fetchCommodities } = useCommodities()

// Local component state
const query = ref('')
const selectedCommodity = ref(props.modelValue)

// Auto-fetch commodities on mount (cached, so safe to call everywhere)
onMounted(() => {
  fetchCommodities()
})

// Filtered commodities based on query and props
const filteredCommodities = computed(() => {
  let commodities = commodityCodes.value

  // Filter by commodity types
  if (props.commodityTypes?.length) {
    commodities = commodities.filter(code => {
      const commodity = commodityDetails.value.find(c => c.code === code)
      return commodity && commodity.type && props.commodityTypes!.includes(commodity.type)
    })
  }

  // Filter by include pattern
  if (props.includePattern) {
    const pattern = typeof props.includePattern === 'string' 
      ? new RegExp(props.includePattern, 'i')
      : props.includePattern
    commodities = commodities.filter(code => pattern.test(code))
  }

  // Filter by exclude pattern
  if (props.excludePattern) {
    const pattern = typeof props.excludePattern === 'string'
      ? new RegExp(props.excludePattern, 'i') 
      : props.excludePattern
    commodities = commodities.filter(code => !pattern.test(code))
  }

  // Apply search query filter
  if (query.value === '') {
    return commodities.sort() // Show all when no query
  } else {
    return commodities
      .filter(code => code.toLowerCase().includes(query.value.toLowerCase()))
      .sort()
  }
})

// Custom query commodity (for allowing new commodity codes)
const queryCommodity = computed(() => {
  return query.value === '' ? null : query.value.toUpperCase()
})

// Get commodity info for display
const getCommodityInfo = (code: string): string | null => {
  if (!props.showDetails) return null
  
  const commodity = commodityDetails.value.find(c => c.code === code)
  if (!commodity) return null
  
  const parts = []
  if (commodity.name) parts.push(commodity.name)
  if (commodity.type) parts.push(`(${commodity.type})`)
  
  return parts.length > 0 ? parts.join(' ') : null
}

// Handle commodity selection
const handleCommoditySelection = (value: string) => {
  selectedCommodity.value = value
  emit('update:modelValue', value)
  query.value = '' // Clear search query after selection
}

// Watch for external changes to modelValue
watch(() => props.modelValue, (newValue) => {
  selectedCommodity.value = newValue
})
</script>