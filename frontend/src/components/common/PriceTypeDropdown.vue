<template>
  <Combobox as="div" v-model="selectedType" @update:modelValue="handleSelection">
    <div class="relative">
      <ComboboxInput
        :class="[
          'block w-full rounded-md bg-white py-1.5 pr-12 pl-3 text-base text-gray-900',
          'outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400',
          'focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600',
          'sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10',
          'dark:placeholder:text-gray-500 dark:focus:outline-indigo-500',
          customClass
        ]"
        :placeholder="placeholder"
        :display-value="(type: unknown) => (typeof type === 'string' ? type : '')"
        autocomplete="off"
        @change="query = $event.target.value"
        @blur="query = ''"
      />
      <ComboboxButton
        class="absolute inset-y-0 right-0 flex items-center rounded-r-md px-2 focus:outline-hidden"
      >
        <ChevronDownIcon
          class="size-5 text-gray-400"
          aria-hidden="true"
        />
      </ComboboxButton>

      <transition
        leave-active-class="transition ease-in duration-100"
        leave-from-class=""
        leave-to-class="opacity-0"
      >
        <ComboboxOptions
          class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
        >
          <!-- Empty option -->
          <ComboboxOption
            :value="''"
            as="template"
            v-slot="{ active }"
          >
            <li :class="[
              'relative cursor-default px-3 py-2 select-none',
              active ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' : 'text-gray-900 dark:text-white'
            ]">
              <span class="block truncate text-gray-400 dark:text-gray-500">
                (none)
              </span>
            </li>
          </ComboboxOption>

          <!-- @ (per-unit) option -->
          <ComboboxOption
            :value="'@'"
            as="template"
            v-slot="{ active }"
          >
            <li :class="[
              'relative cursor-default px-3 py-2 select-none',
              active ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' : 'text-gray-900 dark:text-white'
            ]">
              <span class="block truncate">
                @ (per-unit)
              </span>
            </li>
          </ComboboxOption>

          <!-- @@ (total) option -->
          <ComboboxOption
            :value="'@@'"
            as="template"
            v-slot="{ active }"
          >
            <li :class="[
              'relative cursor-default px-3 py-2 select-none',
              active ? 'bg-indigo-600 text-white outline-hidden dark:bg-indigo-500' : 'text-gray-900 dark:text-white'
            ]">
              <span class="block truncate">
                @@ (total)
              </span>
            </li>
          </ComboboxOption>
        </ComboboxOptions>
      </transition>
    </div>
  </Combobox>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/vue'

interface Props {
  modelValue: string
  placeholder?: string
  customClass?: string
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: '',
  customClass: ''
})

interface Emits {
  (e: 'update:modelValue', value: string): void
}

const emit = defineEmits<Emits>()

const selectedType = ref(props.modelValue)
const query = ref('')

// Watch for external changes to modelValue
watch(() => props.modelValue, (newValue) => {
  selectedType.value = newValue
})

const handleSelection = (value: string) => {
  emit('update:modelValue', value)
}
</script>
