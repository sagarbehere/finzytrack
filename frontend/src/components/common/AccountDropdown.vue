<template>
  <Combobox as="div" v-model="selectedAccount" @update:modelValue="handleAccountSelection">
    <ComboboxLabel 
      v-if="label" 
      class="block text-sm/6 font-medium text-gray-900 dark:text-white"
    >
      {{ label }}
    </ComboboxLabel>
    <div ref="inputWrapperRef" :class="['relative', label ? 'mt-2' : '']">
      <ComboboxInput
        :class="[
          'block w-full rounded-md bg-white py-1.5 pr-12 pl-3 text-base text-gray-900',
          'outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400',
          'focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600',
          'sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10',
          'dark:placeholder:text-gray-500 dark:focus:outline-indigo-500',
          'disabled:bg-gray-100 disabled:cursor-not-allowed dark:disabled:bg-gray-700',
          customClass
        ]"
        :disabled="isLoading || disabled"
        :placeholder="placeholder"
        @change="query = $event.target.value"
        @blur="query = ''"
        @focus="updatePosition"
        :display-value="(account: unknown) => (typeof account === 'string' ? account : '')"
        autocomplete="off"
      />
      <ComboboxButton
        class="absolute inset-y-0 right-0 flex items-center rounded-r-md px-2 focus:outline-hidden"
        :disabled="isLoading || disabled"
        @click="updatePosition"
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

      <Teleport to="body">
        <transition
          leave-active-class="transition ease-in duration-100"
          leave-from-class=""
          leave-to-class="opacity-0"
        >
          <ComboboxOptions
            v-if="(filteredAccounts.length > 0 || query.length > 0) && !isLoading"
            class="fixed z-[9999] max-h-60 min-w-[320px] w-max overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
            :style="dropdownStyle"
          >
            <!-- Custom query option - allows user to type new account name -->
            <ComboboxOption
              v-if="queryAccount && allowCustom"
              :value="queryAccount"
              as="template"
              v-slot="{ active }"
            >
              <li :class="[
                'relative cursor-default px-3 py-2 select-none',
                active
                  ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500'
                  : 'text-gray-900 dark:text-white'
              ]"
              :title="query"
              >
                <span class="block truncate">
                  {{ query }}
                  <span class="text-sm opacity-75 ml-2">(custom)</span>
                </span>
              </li>
            </ComboboxOption>

            <!-- Filtered account options -->
            <ComboboxOption
              v-for="accountName in filteredAccounts"
              :key="accountName"
              :value="accountName"
              as="template"
              v-slot="{ active }"
            >
              <li :class="[
                'relative cursor-default px-3 py-2 select-none',
                active
                  ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500'
                  : 'text-gray-900 dark:text-white'
              ]"
              :title="accountName"
              >
                <span class="block truncate">
                  {{ accountName }}
                </span>
              </li>
            </ComboboxOption>

            <!-- No results message -->
            <div
              v-if="filteredAccounts.length === 0 && query.length > 0 && !allowCustom"
              class="px-3 py-2 text-gray-500 dark:text-gray-400 text-sm"
            >
              No accounts found matching "{{ query }}"
            </div>
          </ComboboxOptions>
        </transition>
      </Teleport>
    </div>
  </Combobox>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxLabel,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/vue'
import { useAccounts } from '@/composables/useAccounts'
import { useDropdownPosition } from '@/composables/useDropdownPosition'

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
  accountTypes?: string[] // e.g., ['Assets', 'Liabilities']
  openOnly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  placeholder: 'Search accounts...',
  disabled: false,
  customClass: '',
  allowCustom: false,
  openOnly: true
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { accountNames, accountDetails, isLoading, fetchAccounts } = useAccounts()

// Local component state
const query = ref('')
const selectedAccount = ref(props.modelValue)
const inputWrapperRef = ref<HTMLElement | null>(null)
const { dropdownStyle, updatePosition } = useDropdownPosition(inputWrapperRef)

// Auto-fetch accounts on mount (cached, so safe to call everywhere)
onMounted(() => {
  fetchAccounts()
})

// Filtered accounts based on query and props
const filteredAccounts = computed(() => {
  let accounts = accountNames.value

  // Filter by account types (Assets:*, Liabilities:*, etc.)
  if (props.accountTypes?.length) {
    accounts = accounts.filter(name => 
      props.accountTypes!.some(type => name.startsWith(`${type}:`))
    )
  }

  // Filter by include pattern
  if (props.includePattern) {
    const pattern = typeof props.includePattern === 'string' 
      ? new RegExp(props.includePattern, 'i')
      : props.includePattern
    accounts = accounts.filter(name => pattern.test(name))
  }

  // Filter by exclude pattern
  if (props.excludePattern) {
    const pattern = typeof props.excludePattern === 'string'
      ? new RegExp(props.excludePattern, 'i') 
      : props.excludePattern
    accounts = accounts.filter(name => !pattern.test(name))
  }

  // Filter open accounts only
  if (props.openOnly) {
    const openAccountNames = accountDetails.value
      .filter(account => !account.close_date)
      .map(account => account.name)
    accounts = accounts.filter(name => openAccountNames.includes(name))
  }

  // Apply search query filter
  if (query.value === '') {
    return accounts.sort() // Show all when no query
  } else {
    return accounts
      .filter(name => name.toLowerCase().includes(query.value.toLowerCase()))
      .sort()
  }
})

// Custom query account (for allowing new account names)
const queryAccount = computed(() => {
  return query.value === '' ? null : query.value
})

// Handle account selection
const handleAccountSelection = (value: string) => {
  selectedAccount.value = value
  emit('update:modelValue', value)
  query.value = '' // Clear search query after selection
}

// Watch for external changes to modelValue
import { watch } from 'vue'
watch(() => props.modelValue, (newValue) => {
  selectedAccount.value = newValue
})
</script>