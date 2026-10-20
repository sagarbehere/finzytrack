<template>
  <Combobox as="div" :model-value="modelValue || null" nullable @update:model-value="select">
    <div ref="inputWrapperRef" class="relative">
      <ComboboxInput
        :class="[
          'block w-full rounded-md bg-white py-1.5 pr-8 pl-3 text-base text-gray-900',
          'outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400',
          'focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600',
          'sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10',
          'dark:placeholder:text-gray-500 dark:focus:outline-indigo-500',
          isCustom ? 'pr-24' : '',
          customClass,
        ]"
        :placeholder="placeholder"
        :aria-label="ariaLabel"
        :display-value="(v: unknown) => (typeof v === 'string' ? v : '')"
        autocomplete="off"
        @change="query = $event.target.value"
        @blur="query = ''"
        @focus="updatePosition"
      />
      <span
        v-if="isCustom"
        class="pointer-events-none absolute inset-y-0 right-7 my-auto inline-flex h-fit items-center rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
      >
        custom
      </span>
      <ComboboxButton
        class="absolute inset-y-0 right-0 flex items-center rounded-r-md px-2 focus:outline-hidden"
        @click="updatePosition"
      >
        <ChevronDownIcon class="size-5 text-gray-400" aria-hidden="true" />
      </ComboboxButton>

      <Teleport to="body">
        <transition leave-active-class="transition ease-in duration-100" leave-from-class="" leave-to-class="opacity-0">
          <ComboboxOptions
            v-if="filtered.length > 0 || customQuery"
            class="fixed z-[9999] max-h-60 w-max overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
            :style="dropdownStyle"
          >
            <!-- Typed value not in the recognized list: offered as a custom value -->
            <ComboboxOption v-if="customQuery" :value="customQuery" as="template" v-slot="{ active }">
              <li
                :class="[
                  'relative cursor-default px-3 py-2 select-none',
                  active ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' : 'text-gray-900 dark:text-white',
                ]"
              >
                <span class="block truncate">
                  {{ customQuery }}
                  <span class="ml-2 text-sm opacity-75">(custom)</span>
                </span>
              </li>
            </ComboboxOption>

            <ComboboxOption v-for="option in filtered" :key="option" :value="option" as="template" v-slot="{ active }">
              <li
                :class="[
                  'relative cursor-default px-3 py-2 select-none',
                  active ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' : 'text-gray-900 dark:text-white',
                ]"
              >
                <span class="block truncate">{{ option }}</span>
              </li>
            </ComboboxOption>
          </ComboboxOptions>
        </transition>
      </Teleport>
    </div>
  </Combobox>
</template>

<script setup lang="ts">
/**
 * A combobox over a list of recognized values that also accepts any typed value
 * (shown with a "custom" badge). Clearing the input sets the value to ''.
 * Used for recognized-but-not-required metadata (dev-docs/investment-view.md §10.1).
 */
import { computed, ref } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'
import { Combobox, ComboboxButton, ComboboxInput, ComboboxOption, ComboboxOptions } from '@headlessui/vue'
import { useDropdownPosition } from '@/composables/useDropdownPosition'

interface Props {
  modelValue: string
  options: string[]
  placeholder?: string
  ariaLabel?: string
  customClass?: string
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: '',
  ariaLabel: undefined,
  customClass: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const query = ref('')
const inputWrapperRef = ref<HTMLElement | null>(null)
const { dropdownStyle, updatePosition } = useDropdownPosition(inputWrapperRef)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q === '' ? props.options : props.options.filter(o => o.toLowerCase().includes(q))
})

// The typed text, when it isn't (case-insensitively) one of the options.
const customQuery = computed(() => {
  const q = query.value.trim()
  if (q === '') return null
  return props.options.some(o => o.toLowerCase() === q.toLowerCase()) ? null : q
})

const isCustom = computed(() => {
  const v = props.modelValue.trim().toLowerCase()
  return v !== '' && !props.options.some(o => o.toLowerCase() === v)
})

function select(value: string | null) {
  emit('update:modelValue', value ?? '')
  query.value = ''
}
</script>
