<template>
  <TransitionRoot :show="open" as="template">
    <Dialog class="relative z-40" @close="emit('update:open', false)">
      <TransitionChild
        as="template"
        enter="ease-out duration-300" enter-from="opacity-0" enter-to="opacity-100"
        leave="ease-in duration-200" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-gray-500/75 transition-opacity dark:bg-gray-900/50" />
      </TransitionChild>

      <div class="fixed inset-0 z-10 w-screen overflow-y-auto">
        <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
          <TransitionChild
            as="template"
            enter="ease-out duration-300" enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95" enter-to="opacity-100 translate-y-0 sm:scale-100"
            leave="ease-in duration-200" leave-from="opacity-100 translate-y-0 sm:scale-100" leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
          >
            <DialogPanel class="relative transform rounded-lg bg-white px-4 pt-5 pb-4 text-left shadow-xl transition-all sm:my-8 sm:w-full sm:max-w-2xl sm:p-6 dark:bg-gray-800 dark:outline dark:-outline-offset-1 dark:outline-white/10">
              <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white">
                Find the Indian mutual fund for {{ commodity }}
              </DialogTitle>
              <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Pick the scheme whose name matches your plan (Direct or Regular) and option
                (Growth or IDCW). Its NAV should match the NAV your broker shows.
              </p>

              <form class="mt-4 flex gap-2" @submit.prevent="search">
                <input
                  v-model="query"
                  type="search"
                  aria-label="Fund name"
                  placeholder="Fund name, e.g. Parag Parikh Flexi Cap"
                  class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                />
                <button
                  type="submit"
                  :disabled="loading"
                  class="shrink-0 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
                >
                  {{ loading ? 'Searching…' : 'Search' }}
                </button>
              </form>

              <!-- Problems: a short query, or the search service failing -->
              <p v-if="hint" class="mt-3 text-sm text-gray-500 dark:text-gray-400">{{ hint }}</p>
              <div v-if="failed" class="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400" data-test="amfi-error">
                Could not search for funds. If you know the scheme code, type <code>amfi:</code> and the
                code into Price symbol yourself.
              </div>
              <div v-if="data?.nav_warning" class="mt-3 rounded-md bg-yellow-50 p-3 text-sm text-yellow-800 dark:bg-yellow-500/10 dark:text-yellow-300">
                {{ data.nav_warning }}
              </div>

              <!-- Results -->
              <p v-if="data && data.schemes.length === 0" class="mt-4 text-sm text-gray-500 dark:text-gray-400">
                No funds match “{{ searched }}”. Try fewer or different words.
              </p>
              <div v-if="data && data.schemes.length > 0" class="mt-4 max-h-96 overflow-y-auto">
                <table class="min-w-full divide-y divide-gray-300 dark:divide-white/15">
                  <thead>
                    <tr>
                      <th scope="col" class="py-2 pr-3 text-left text-sm font-semibold text-gray-900 dark:text-white">Scheme</th>
                      <th scope="col" class="px-3 py-2 text-right text-sm font-semibold text-gray-900 dark:text-white">NAV</th>
                      <th scope="col" class="py-2 pl-3"><span class="sr-only">Use</span></th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-200 dark:divide-white/10">
                    <tr v-for="s in data.schemes" :key="s.scheme_code" :class="isCurrent(s) ? '' : 'opacity-60'">
                      <td class="py-2 pr-3 text-sm text-gray-900 dark:text-white">
                        {{ s.scheme_name }}
                        <div class="text-xs text-gray-500 dark:text-gray-400">Code {{ s.scheme_code }}</div>
                      </td>
                      <td class="px-3 py-2 text-right text-sm whitespace-nowrap text-gray-900 dark:text-white">
                        <template v-if="s.nav">
                          {{ formatPrice(s.nav) }}
                          <div class="text-xs text-gray-500 dark:text-gray-400">
                            {{ isCurrent(s) ? '' : 'Last NAV ' }}{{ formatShortDate(s.nav_date!, today) }}
                          </div>
                        </template>
                        <span v-else class="text-gray-400 dark:text-gray-500">—</span>
                      </td>
                      <td class="py-2 pl-3 text-right">
                        <button
                          type="button"
                          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                          @click="use(s)"
                        >
                          Use
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="data.total > data.schemes.length" class="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  Showing {{ data.schemes.length }} of {{ data.total }} matches. Add words to narrow the search.
                </p>
              </div>

              <div class="mt-5 flex justify-end">
                <button
                  type="button"
                  class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                  @click="emit('update:open', false)"
                >
                  Cancel
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
/**
 * Finds an Indian mutual fund's AMFI scheme code by name, so the user can set a
 * holding's Price symbol to `amfi:<code>`. Search failures (mfapi.in offline or
 * changed) explain themselves: a toast with the server's reason, and an inline
 * note that the code can still be typed by hand.
 */
import { ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { PricesService, type AmfiScheme, type AmfiSearchData } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'
import { formatPrice, formatShortDate } from '@/utils/prices'
import { todayLocal } from '@/utils/date'

const props = defineProps<{
  open: boolean
  /** The holding's commodity code, for the title. */
  commodity: string
  /** What to search for when the dialog opens (the holding's name, else its code). */
  initialQuery: string
}>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  /** The chosen scheme's code (the caller sets Price symbol to `amfi:<code>`). */
  select: [schemeCode: string]
}>()

// A NAV older than this means the scheme is no longer priced (closed or merged).
const STALE_DAYS = 30
// Search failures are shown inline, so the reason goes to a toast, not a modal over the dialog.
const SEARCH_ERROR_DISPLAY = { PRICE_SOURCE_UNAVAILABLE: 'Toast', PRICE_SOURCE_CHANGED: 'Toast' } as const

const today = todayLocal()
const query = ref('')
const searched = ref('')
const data = ref<AmfiSearchData | null>(null)
const loading = ref(false)
const failed = ref(false)
const hint = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) return
    query.value = props.initialQuery
    data.value = null
    failed.value = false
    hint.value = ''
    if (query.value.trim().length >= 3) search()
  },
  { immediate: true },
)

async function search() {
  const q = query.value.trim()
  failed.value = false
  hint.value = ''
  if (q.length < 3) {
    hint.value = 'Type at least 3 letters of the fund name.'
    return
  }
  loading.value = true
  try {
    const resp = await PricesService.searchAmfiSchemes(q)
    data.value = resp.data ?? { schemes: [], total: 0 }
    searched.value = q
  } catch (err) {
    data.value = null
    failed.value = true
    errorHandler.display(err, SEARCH_ERROR_DISPLAY)
  } finally {
    loading.value = false
  }
}

function isCurrent(s: AmfiScheme): boolean {
  if (!s.nav_date) return false
  const cutoff = new Date(`${today}T00:00:00`)
  cutoff.setDate(cutoff.getDate() - STALE_DAYS)
  return new Date(`${s.nav_date}T00:00:00`) >= cutoff
}

function use(s: AmfiScheme) {
  emit('select', s.scheme_code)
  emit('update:open', false)
}
</script>
