<template>
  <Listbox :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)">
    <div class="relative">
      <ListboxButton
        class="relative w-full cursor-default rounded-md bg-white py-1.5 pl-3 pr-8 text-left text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10"
      >
        <span class="block truncate" :class="!modelValue ? 'text-gray-400 dark:text-gray-500' : ''">
          {{ modelValue || placeholder }}
        </span>
        <span class="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2">
          <ChevronUpDownIcon class="h-4 w-4 text-gray-400" aria-hidden="true" />
        </span>
      </ListboxButton>

      <transition leave-active-class="transition ease-in duration-100" leave-from-class="opacity-100" leave-to-class="opacity-0">
        <ListboxOptions
          class="absolute z-40 mt-1 max-h-56 w-full overflow-auto rounded-md bg-white py-1 text-sm shadow-lg ring-1 ring-black/5 focus:outline-none dark:bg-gray-800 dark:ring-white/10"
        >
          <ListboxOption
            v-for="option in options"
            :key="option"
            :value="option"
            v-slot="{ active, selected }"
            as="template"
          >
            <li
              :class="[
                'relative cursor-default select-none py-2 pl-3 pr-9',
                active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white',
              ]"
            >
              <span :class="['block truncate', selected ? 'font-semibold' : 'font-normal']" :title="option">{{ option }}</span>
            </li>
          </ListboxOption>
          <li v-if="options.length === 0" class="px-3 py-2 text-gray-500 dark:text-gray-400">No options</li>
        </ListboxOptions>
      </transition>
    </div>
  </Listbox>
</template>

<script setup lang="ts">
import { Listbox, ListboxButton, ListboxOptions, ListboxOption } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/20/solid'

defineProps<{
  modelValue: string
  options: string[]
  placeholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()
</script>
