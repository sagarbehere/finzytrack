<template>
  <form class="space-y-4" novalidate @submit.prevent="submit">
    <div :class="['grid grid-cols-1 gap-4', fixedCommodity ? 'sm:grid-cols-3' : 'sm:grid-cols-2']">
      <div v-if="!fixedCommodity">
        <CommodityDropdown
          v-model="form.commodity"
          label="Commodity"
          :allow-custom="true"
          placeholder="e.g. VOO"
        />
        <p v-if="shownErrors.commodity" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ shownErrors.commodity }}</p>
      </div>
      <div>
        <label :for="`${uid}-date`" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Date</label>
        <input
          :id="`${uid}-date`"
          v-model="form.date"
          type="date"
          class="mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
        />
        <p v-if="shownErrors.date" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ shownErrors.date }}</p>
      </div>
      <div>
        <label :for="`${uid}-number`" class="block text-sm/6 font-medium text-gray-900 dark:text-white">Price of one unit</label>
        <input
          :id="`${uid}-number`"
          v-model="form.number"
          type="text"
          inputmode="decimal"
          placeholder="0.00"
          class="mt-2 block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
        />
        <p v-if="shownErrors.number" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ shownErrors.number }}</p>
      </div>
      <div>
        <CommodityDropdown
          v-model="form.quote"
          label="In currency"
          :currencies-only="true"
          :allow-custom="true"
          placeholder="e.g. USD"
        />
        <p v-if="shownErrors.quote" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ shownErrors.quote }}</p>
      </div>
    </div>

    <div class="flex justify-end gap-3">
      <button
        v-if="cancelable"
        type="button"
        class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        @click="emit('cancel')"
      >
        Cancel
      </button>
      <button
        type="submit"
        :disabled="saving"
        class="rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
      >
        {{ saving ? 'Saving…' : 'Add price' }}
      </button>
    </div>
  </form>
</template>

<script lang="ts">
let formCount = 0
</script>

<script setup lang="ts">
/**
 * Add a price (or replace the one at the same date). With `fixedCommodity` the
 * commodity is the selected row's and only date, price and currency are asked.
 */
import { computed, reactive, ref, watch } from 'vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import { todayLocal } from '@/utils/date'
import { validatePriceInput, type PriceInput } from '@/utils/prices'

interface Props {
  fixedCommodity?: string
  defaultQuote?: string
  saving?: boolean
  cancelable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  fixedCommodity: '',
  defaultQuote: '',
  saving: false,
  cancelable: false,
})

const emit = defineEmits<{
  submit: [input: PriceInput]
  cancel: []
}>()

// Unique ids for label/input pairs (two forms can be on screen at once).
const uid = `price-form-${++formCount}`

const form = reactive<PriceInput>({
  date: todayLocal(),
  commodity: props.fixedCommodity,
  quote: props.defaultQuote,
  number: '',
})

watch(() => [props.fixedCommodity, props.defaultQuote], () => reset())

const submitted = ref(false)
const errors = computed(() => validatePriceInput(form))
// Errors show after the first submit attempt, then update as the user types.
const shownErrors = computed(() => (submitted.value ? errors.value : {}))

function submit() {
  submitted.value = true
  if (Object.keys(errors.value).length > 0) return
  emit('submit', { ...form, commodity: props.fixedCommodity || form.commodity })
}

/** Back to defaults: today, empty price, the default currency. */
function reset() {
  form.date = todayLocal()
  form.commodity = props.fixedCommodity
  form.quote = props.defaultQuote
  form.number = ''
  submitted.value = false
}

defineExpose({ reset })
</script>
