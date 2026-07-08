<template>
  <Listbox as="div" v-model="selectedInterval" @update:modelValue="handleSelection">
    <div ref="inputWrapperRef" class="relative">
      <ListboxButton
        class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500"
        @click="updatePosition"
        @keydown="updatePosition"
      >
        <span class="col-start-1 row-start-1 truncate pr-6 capitalize">{{ selectedInterval }}</span>
        <ChevronUpDownIcon
          class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400"
          aria-hidden="true"
        />
      </ListboxButton>

      <Teleport to="body">
        <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
          <ListboxOptions
            class="fixed z-[9999] max-h-60 w-max min-w-[8rem] overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
            :style="dropdownStyle"
          >
            <ListboxOption
              v-for="interval in INTERVALS"
              :key="interval"
              :value="interval"
              as="template"
              v-slot="{ active, selected }"
            >
              <li
                :class="[
                  active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white',
                  'relative cursor-default py-2 pr-9 pl-3 capitalize select-none',
                ]"
              >
                <span :class="[selected ? 'font-semibold' : 'font-normal', 'block truncate']">{{ interval }}</span>
                <span
                  v-if="selected"
                  :class="[
                    active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400',
                    'absolute inset-y-0 right-0 flex items-center pr-4',
                  ]"
                >
                  <CheckIcon class="size-5" aria-hidden="true" />
                </span>
              </li>
            </ListboxOption>
          </ListboxOptions>
        </transition>
      </Teleport>
    </div>
  </Listbox>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
import { useDropdownPosition } from '@/composables/useDropdownPosition'

// The Beancount `custom "budget"` intervals (see dev-docs/budget.md §3).
const INTERVALS = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly'] as const

interface Props {
  modelValue: string
}

const props = defineProps<Props>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const selectedInterval = ref(props.modelValue)
const inputWrapperRef = ref<HTMLElement | null>(null)
const { dropdownStyle, updatePosition } = useDropdownPosition(inputWrapperRef)

watch(
  () => props.modelValue,
  (newValue) => {
    selectedInterval.value = newValue
  },
)

const handleSelection = (value: string) => {
  emit('update:modelValue', value)
}
</script>
