<template>
  <div class="flex flex-wrap items-center gap-2 sm:gap-4">
    <div
      v-for="param in visibleParameters"
      :key="param.name"
      class="flex shrink-0 items-center gap-2"
    >
      <!-- Select input -->
      <template v-if="param.type === 'select'">
        <Listbox as="div" :model-value="modelValue[param.name]" @update:model-value="(val: string | number) => updateParam(param.name, val)" class="flex items-center gap-2">
          <ListboxLabel class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ param.label }}</ListboxLabel>
          <div class="relative">
            <ListboxButton class="grid w-full cursor-default grid-cols-1 rounded-md bg-white py-1.5 pr-2 pl-3 text-left text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus-visible:outline-indigo-500">
              <span class="col-start-1 row-start-1 truncate pr-6">{{ getButtonLabel(param) }}</span>
              <ChevronUpDownIcon class="col-start-1 row-start-1 size-5 self-center justify-self-end text-gray-500 sm:size-4 dark:text-gray-400" aria-hidden="true" />
            </ListboxButton>
            <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
              <ListboxOptions class="absolute right-0 z-30 mt-1 max-h-60 min-w-max w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline-1 outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                <ListboxOption v-for="option in getOptions(param)" :key="String(option.value)" :value="option.value" as="template" v-slot="{ active, selected }">
                  <li :class="[active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white', 'relative cursor-default py-2 pr-9 pl-3 select-none']">
                    <span :class="[selected ? 'font-semibold' : 'font-normal', isGenSelection(option.value) ? 'italic' : '', 'block truncate']">{{ option.label }}</span>
                    <span v-if="selected" :class="[active ? 'text-white' : 'text-indigo-600 dark:text-indigo-400', 'absolute inset-y-0 right-0 flex items-center pr-4']">
                      <CheckIcon class="size-5" aria-hidden="true" />
                    </span>
                  </li>
                </ListboxOption>
              </ListboxOptions>
            </transition>
          </div>
        </Listbox>
      </template>

      <template v-else>
        <label
          :for="`param-${param.name}`"
          class="text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          {{ param.label }}
        </label>

        <!-- Number input -->
        <input
          v-if="param.type === 'number'"
          :id="`param-${param.name}`"
          type="number"
          :value="resolveParameterValue(modelValue[param.name])"
          :min="boundFor(param, 'min') ?? param.min"
          :max="boundFor(param, 'max') ?? param.max"
          @input="updateParam(param.name, Number(($event.target as HTMLInputElement).value))"
          class="block w-24 rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />

        <!-- Date input -->
        <input
          v-else-if="param.type === 'date'"
          :id="`param-${param.name}`"
          type="date"
          :value="resolveParameterValue(modelValue[param.name])"
          :min="boundFor(param, 'min')"
          :max="boundFor(param, 'max')"
          @input="updateParam(param.name, ($event.target as HTMLInputElement).value)"
          class="block rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
        />

        <!-- Boolean input (checkbox); stored as 'true'/'false' strings -->
        <input
          v-else-if="param.type === 'boolean'"
          :id="`param-${param.name}`"
          type="checkbox"
          :checked="isChecked(modelValue[param.name])"
          @change="updateParam(param.name, ($event.target as HTMLInputElement).checked ? 'true' : 'false')"
          class="size-4 rounded border-gray-300 text-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:border-white/20 dark:bg-white/5"
        />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Listbox, ListboxButton, ListboxLabel, ListboxOption, ListboxOptions } from '@headlessui/vue'
import { ChevronUpDownIcon } from '@heroicons/vue/16/solid'
import { CheckIcon } from '@heroicons/vue/20/solid'
import type { RecipeParameter, RecipeParameterOption } from '@/types/recipes'
import { useCommodities } from '@/composables/useCommodities'
import { useAvailableYears } from '@/composables/useAvailableYears'
import { useAccounts } from '@/composables/useAccounts'
import { useBudgets } from '@/composables/useBudgets'
import {
  isGenSelection,
  genSelectionName,
  resolveParameterValue,
  GENERATOR_LABELS,
} from '@/recipes/functions'

interface Props {
  parameters: RecipeParameter[]
  modelValue: Record<string, string | number>
}

interface Emits {
  (e: 'update:modelValue', value: Record<string, string | number>): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

// Hidden params stay functional (default, select-target, URL) but render no
// control. A `showWhen` param renders only while another param has the given
// value (e.g. a date revealed by a checkbox); it stays functional when hidden.
const visibleParameters = computed(() =>
  props.parameters.filter((p) => {
    if (p.hidden) return false
    if (p.showWhen) {
      return String(props.modelValue[p.showWhen.param] ?? '') === String(p.showWhen.equals)
    }
    return true
  }),
)

/** A boolean param stores 'true'/'false'; treat either the string or a real bool. */
function isChecked(value: unknown): boolean {
  return value === 'true' || value === true
}

/** Resolve a min/max bound from a referenced parameter's current value (minParam/
 * maxParam), so e.g. a 'from' date can't exceed the 'as of' date. Reactive. */
function boundFor(param: RecipeParameter, which: 'min' | 'max'): string | number | undefined {
  const ref = which === 'min' ? param.minParam : param.maxParam
  if (!ref) return undefined
  const raw = props.modelValue[ref]
  if (raw === undefined || raw === '') return undefined
  return resolveParameterValue(raw)
}

const { commodityDetails, fetchCommodities } = useCommodities()
const dynamicCurrencyOptions = ref<RecipeParameterOption[]>([])

const { years: dynamicYearOptions, fetchYears } = useAvailableYears()

const { accountNames, fetchAccounts } = useAccounts()

const { fetch: fetchBudgets } = useBudgets()
// Total-account options (optionsFrom: 'budgetTotals'), derived from the ledger's
// budget directives at mount.
const dynamicBudgetTotalOptions = ref<RecipeParameterOption[]>([])

// optionsFrom values that populate from the accounts list, mapped to the type
// root each restricts to (undefined = all types).
const ACCOUNT_OPTION_TYPES: Record<string, string | undefined> = {
  accounts: undefined,
  expenseAccounts: 'Expenses',
  incomeAccounts: 'Income',
}

/** Budgeted accounts that have at least one budgeted descendant — the valid
 * top-down "total" accounts (they have carve-outs under them). Includes quoted
 * roots like 'Expenses'. value = account path; label = "All <Root>" for a root,
 * else the path below the type root. */
function deriveBudgetTotals(budgetAccounts: string[]): RecipeParameterOption[] {
  const distinct = [...new Set(budgetAccounts)]
  const totals = distinct.filter((a) => distinct.some((b) => b !== a && b.startsWith(a + ':')))
  totals.sort()
  return totals.map((a) => {
    const below = a.split(':').slice(1).join(':')
    return { value: a, label: below === '' ? `All ${a}` : below }
  })
}

/** Options from the ledger's accounts: value = full path, label = path below its
 * type root (e.g. 'Expenses:Insurance:Health' → 'Insurance:Health'). */
function accountOptions(filterType: string | undefined): RecipeParameterOption[] {
  return accountNames.value
    .filter((n) => !filterType || n === filterType || n.startsWith(filterType + ':'))
    .map((n) => {
      const below = n.split(':').slice(1).join(':')
      return { value: n, label: below || n }
    })
}

function getRawOptions(param: RecipeParameter): RecipeParameterOption[] {
  if (param.optionsFrom === 'currencies') {
    return dynamicCurrencyOptions.value
  }
  if (param.optionsFrom === 'years') {
    return dynamicYearOptions.value
  }
  if (param.optionsFrom && param.optionsFrom in ACCOUNT_OPTION_TYPES) {
    return accountOptions(ACCOUNT_OPTION_TYPES[param.optionsFrom])
  }
  if (param.optionsFrom === 'budgetTotals') {
    return dynamicBudgetTotalOptions.value
  }
  // `options` is typed as `RecipeParameterOption[] | { $gen: ... }` per the
  // JSON schema. resolveGenerators (run by useRecipeLoader on every JSON
  // recipe before it reaches this component) replaces $gen objects on the
  // `options` field with their resolved arrays, so by this point it's an array.
  return (param.options as RecipeParameterOption[] | undefined) || []
}

/**
 * Options shown in the dropdown. If the parameter's default is a templated
 * generator sentinel (e.g. "$gen:currentMonth"), prepend that as a sticky
 * option so the user can opt back into the templated value after picking a
 * literal — it re-evaluates on each dashboard load instead of pinning a
 * specific value.
 */
function getOptions(param: RecipeParameter): RecipeParameterOption[] {
  const base = getRawOptions(param)
  const def = param.default
  if (typeof def === 'string' && isGenSelection(def)) {
    const name = genSelectionName(def)
    const label = GENERATOR_LABELS[name] ?? name
    // Show the live resolved value in parens so the user can tell at a glance
    // what the template currently means (e.g. "Current Month (May)"). If the
    // resolved value matches a labeled option (e.g. month number 5 → "May"),
    // prefer that label over the raw value.
    const resolved = resolveParameterValue(def)
    const resolvedLabel = base.find((o) => o.value === resolved)?.label ?? String(resolved)
    return [{ value: def, label: `${label} (${resolvedLabel})` }, ...base]
  }
  return base
}

function getButtonLabel(param: RecipeParameter): string {
  const value = props.modelValue[param.name]
  const match = getOptions(param).find((o) => o.value === value)
  if (match) return match.label
  return String(value)
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
  // Only visible params need their dropdown options fetched.
  const hasDynamicCurrencies = visibleParameters.value.some((p) => p.optionsFrom === 'currencies')
  const hasDynamicYears = visibleParameters.value.some((p) => p.optionsFrom === 'years')

  const fetches: Promise<void>[] = []

  if (hasDynamicCurrencies) {
    fetches.push(
      fetchCommodities().then(() => {
        dynamicCurrencyOptions.value = commodityDetails.value
          .filter((c) => c.type === 'Currency' || c.type === null || c.type === undefined)
          .map((c) => ({ value: c.code, label: c.code }))
      }),
    )
  }

  if (hasDynamicYears) {
    fetches.push(fetchYears())
  }

  const hasDynamicAccounts = visibleParameters.value.some(
    (p) => p.optionsFrom !== undefined && p.optionsFrom in ACCOUNT_OPTION_TYPES,
  )
  if (hasDynamicAccounts) {
    fetches.push(fetchAccounts())
  }

  const hasBudgetTotals = visibleParameters.value.some((p) => p.optionsFrom === 'budgetTotals')
  if (hasBudgetTotals) {
    fetches.push(
      fetchBudgets()
        .then((items) => {
          dynamicBudgetTotalOptions.value = deriveBudgetTotals(items.map((b) => b.account))
        })
        .catch(() => {
          dynamicBudgetTotalOptions.value = []
        }),
    )
  }

  await Promise.all(fetches)
})
</script>
