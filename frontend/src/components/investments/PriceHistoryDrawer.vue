<template>
  <TransitionRoot :show="open" as="template">
    <Dialog class="relative z-40" @close="close">
      <TransitionChild
        as="template"
        enter="ease-in-out duration-500" enter-from="opacity-0" enter-to="opacity-100"
        leave="ease-in-out duration-500" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-gray-500/75 transition-opacity dark:bg-gray-900/50" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-hidden">
        <div class="absolute inset-0 overflow-hidden">
          <div class="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10 sm:pl-16">
            <TransitionChild
              as="template"
              enter="transform transition ease-in-out duration-500 sm:duration-700" enter-from="translate-x-full" enter-to="translate-x-0"
              leave="transform transition ease-in-out duration-500 sm:duration-700" leave-from="translate-x-0" leave-to="translate-x-full"
            >
              <DialogPanel class="pointer-events-auto w-screen max-w-xl">
                <div class="relative flex h-full flex-col overflow-y-auto bg-white py-6 shadow-xl dark:bg-gray-800 dark:after:absolute dark:after:inset-y-0 dark:after:left-0 dark:after:w-px dark:after:bg-white/10">
                  <!-- Header -->
                  <div class="px-4 sm:px-6">
                    <div class="flex items-start justify-between">
                      <div>
                        <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white">
                          {{ row?.commodity }} prices<span v-if="row?.quote_currency"> in {{ row.quote_currency }}</span>
                        </DialogTitle>
                        <p v-if="row" class="mt-1 text-sm text-gray-500 dark:text-gray-400">
                          {{ cadenceLabel(row.cadence) }}<span v-if="row.not_fetched_reason"> · {{ row.not_fetched_reason }}</span>
                        </p>
                      </div>
                      <div class="ml-3 flex h-7 items-center">
                        <button
                          type="button"
                          class="relative rounded-md text-gray-400 hover:text-gray-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:hover:text-white dark:focus-visible:outline-indigo-500"
                          @click="close"
                        >
                          <span class="absolute -inset-2.5" />
                          <span class="sr-only">Close panel</span>
                          <XMarkIcon class="size-6" aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  </div>

                  <div v-if="row" class="relative mt-6 flex-1 space-y-8 px-4 sm:px-6">
                    <!-- Add -->
                    <section>
                      <h3 class="text-sm font-semibold text-gray-900 dark:text-white">Add a price</h3>
                      <div class="mt-3">
                        <PriceForm
                          ref="addFormRef"
                          :fixed-commodity="row.commodity"
                          :default-quote="row.quote_currency ?? ''"
                          :saving="saving"
                          @submit="add"
                        />
                      </div>
                    </section>

                    <!-- History -->
                    <section>
                      <h3 class="text-sm font-semibold text-gray-900 dark:text-white">History</h3>
                      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{{ helpText }}</p>

                      <p v-if="history.isLoading.value && history.prices.value.length === 0" class="mt-4 text-sm text-gray-500 dark:text-gray-400">
                        Loading…
                      </p>
                      <p v-else-if="history.total.value === 0" class="mt-4 text-sm text-gray-500 dark:text-gray-400">
                        No prices yet.
                      </p>

                      <div v-else class="mt-4 overflow-hidden shadow-sm outline-1 outline-black/5 sm:rounded-lg dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
                        <table class="min-w-full divide-y divide-gray-300 dark:divide-white/15">
                          <thead class="bg-gray-50 dark:bg-gray-800/75">
                            <tr>
                              <th scope="col" class="py-3.5 pr-3 pl-4 text-left text-sm font-semibold text-gray-900 sm:pl-6 dark:text-white">Date</th>
                              <th scope="col" class="px-3 py-3.5 text-right text-sm font-semibold text-gray-900 dark:text-white">Price</th>
                              <th scope="col" class="py-3.5 pr-4 pl-3 sm:pr-6"><span class="sr-only">Actions</span></th>
                            </tr>
                          </thead>
                          <tbody class="divide-y divide-gray-200 bg-white dark:divide-white/10 dark:bg-gray-800/50">
                            <tr v-for="p in history.prices.value" :key="p.date">
                              <template v-if="editing && editing.original.date === p.date">
                                <td class="py-2 pr-3 pl-4 sm:pl-6">
                                  <input
                                    v-model="editing.date"
                                    type="date"
                                    :aria-label="`Date for the price on ${p.date}`"
                                    class="block w-full rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                                  />
                                </td>
                                <td class="px-3 py-2">
                                  <input
                                    v-model="editing.number"
                                    type="text"
                                    inputmode="decimal"
                                    :aria-label="`Price on ${p.date}`"
                                    class="block w-full rounded-md bg-white px-3 py-1.5 text-right text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                                    @keydown.enter.prevent="saveEdit"
                                    @keydown.esc.stop.prevent="editing = null"
                                  />
                                  <p v-if="editError" class="mt-1 text-sm text-red-600 dark:text-red-400">{{ editError }}</p>
                                </td>
                                <td class="py-2 pr-4 pl-3 text-right text-sm font-medium whitespace-nowrap sm:pr-6">
                                  <button
                                    type="button"
                                    :disabled="saving"
                                    class="text-indigo-600 hover:text-indigo-900 disabled:opacity-50 dark:text-indigo-400 dark:hover:text-indigo-300"
                                    @click="saveEdit"
                                  >
                                    Save
                                  </button>
                                  <button
                                    type="button"
                                    class="ml-4 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
                                    @click="editing = null"
                                  >
                                    Cancel
                                  </button>
                                </td>
                              </template>
                              <template v-else>
                                <td class="py-3 pr-3 pl-4 text-sm font-medium whitespace-nowrap text-gray-900 sm:pl-6 dark:text-white">
                                  {{ formatShortDate(p.date, today) }}
                                </td>
                                <td class="px-3 py-3 text-right text-sm whitespace-nowrap text-gray-900 tabular-nums dark:text-white">
                                  {{ formatPrice(p.number, p.quote_currency) }}
                                </td>
                                <td class="py-3 pr-4 pl-3 text-right text-sm font-medium whitespace-nowrap sm:pr-6">
                                  <span
                                    v-if="p.source === 'ledger'"
                                    class="inline-flex items-center rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
                                    title="This price is written in your ledger file. Change it there."
                                  >
                                    In ledger file
                                  </span>
                                  <template v-else>
                                    <button
                                      type="button"
                                      class="text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
                                      @click="startEdit(p)"
                                    >
                                      Edit<span class="sr-only">, {{ p.date }}</span>
                                    </button>
                                    <button
                                      type="button"
                                      class="ml-4 text-red-600 hover:text-red-900 dark:text-red-400 dark:hover:text-red-300"
                                      @click="deleting = p"
                                    >
                                      Delete<span class="sr-only">, {{ p.date }}</span>
                                    </button>
                                  </template>
                                </td>
                              </template>
                            </tr>
                          </tbody>
                        </table>
                      </div>

                      <!-- Pagination -->
                      <nav
                        v-if="history.total.value > HISTORY_PAGE_SIZE"
                        class="mt-4 flex items-center justify-between"
                        aria-label="Pagination"
                      >
                        <p class="text-sm text-gray-700 dark:text-gray-300">
                          {{ pageStart + 1 }}–{{ pageStart + history.prices.value.length }} of {{ history.total.value }}
                        </p>
                        <div class="flex gap-3">
                          <button
                            type="button"
                            :disabled="history.page.value === 0 || history.isLoading.value"
                            class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                            @click="goToPage(history.page.value - 1)"
                          >
                            Newer
                          </button>
                          <button
                            type="button"
                            :disabled="pageStart + history.prices.value.length >= history.total.value || history.isLoading.value"
                            class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                            @click="goToPage(history.page.value + 1)"
                          >
                            Older
                          </button>
                        </div>
                      </nav>
                    </section>
                  </div>
                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </div>

      <ConfirmDialog
        :is-open="deleting !== null"
        title="Delete price"
        :message="deleteMessage"
        confirm-text="Delete"
        cancel-text="Cancel"
        variant="danger"
        @confirm="confirmDelete"
        @cancel="deleting = null"
        @close="deleting = null"
      />

      <ConfirmDialog
        :is-open="confirm.isOpen.value"
        :title="confirm.dialogOptions.value.title"
        :message="confirm.dialogOptions.value.message"
        :confirm-text="confirm.dialogOptions.value.confirmText"
        :cancel-text="confirm.dialogOptions.value.cancelText"
        :variant="confirm.dialogOptions.value.variant"
        @confirm="confirm.handleConfirm"
        @cancel="confirm.handleCancel"
        @close="confirm.handleClose"
      />
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
/**
 * One (commodity, quote currency) pair's price history, newest first, with
 * Add / Edit / Delete on prices in the price file. Prices written in the ledger
 * file are listed read-only. See dev-docs/investment-view.md §10.2 (P-Q2, P-Q4).
 */
import { computed, ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { PriceEditRequest, PriceOverviewRow, PricePoint } from '@/services/generated-api'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PriceForm from '@/components/investments/PriceForm.vue'
import {
  HISTORY_PAGE_SIZE,
  usePriceHistory,
  useSaveAskingToReplace,
  type SaveOutcome,
} from '@/composables/usePrices'
import { useToast } from '@/composables/useNotifications'
import {
  addRequest,
  cadenceLabel,
  editRequest,
  formatPrice,
  formatShortDate,
  removeRequest,
  validatePriceInput,
  type PriceInput,
} from '@/utils/prices'

interface Props {
  open: boolean
  row: PriceOverviewRow | null
  today: string
  saving: boolean
  save: (request: PriceEditRequest) => Promise<SaveOutcome>
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  /** Show another pair (a price was added in a currency other than this row's). */
  select: [commodity: string, quote: string]
}>()

const { confirm, saveAsking } = useSaveAskingToReplace(props.save, () => props.today)

const history = usePriceHistory()
const { success: showSuccessToast } = useToast()
const addFormRef = ref<InstanceType<typeof PriceForm> | null>(null)

const editing = ref<{ original: PricePoint; date: string; number: string } | null>(null)
const editError = ref('')
const deleting = ref<PricePoint | null>(null)

// Separate sources, so a reload that only swaps the row object (same pair)
// doesn't reset the drawer.
watch(
  [() => props.open, () => props.row?.commodity, () => props.row?.quote_currency],
  ([open]) => {
    editing.value = null
    deleting.value = null
    if (open && props.row?.quote_currency) history.load(props.row.commodity, props.row.quote_currency)
    else history.clear()
  },
  { immediate: true },
)

const pageStart = computed(() => history.page.value * HISTORY_PAGE_SIZE)

const helpText = computed(() =>
  props.row?.cadence === 'never'
    ? 'Prices for this commodity are not fetched. Add them here.'
    : 'Each update replaces the newest price with the fetched price, even a price you entered. ' +
      'Deleting the newest price makes the next update fetch that day again. Deleting an older price leaves a gap; older days are not fetched again.',
)

const deleteMessage = computed(() =>
  deleting.value
    ? `Delete the price of ${formatPrice(deleting.value.number, deleting.value.quote_currency)} on ${formatShortDate(deleting.value.date, props.today)}?`
    : '',
)

function close() {
  emit('update:open', false)
}

function goToPage(page: number) {
  if (!props.row?.quote_currency) return
  editing.value = null
  history.load(props.row.commodity, props.row.quote_currency, page)
}

/** Save (asking before replacing a price on a taken date), then reload this
 * page of history. Returns true on success. */
async function apply(
  request: PriceEditRequest, message: (replaced: boolean) => string,
): Promise<boolean> {
  const result = await saveAsking(request)
  if (!result) return false
  showSuccessToast('Saved', message(result.replaced))
  if (props.row?.quote_currency) {
    await history.load(props.row.commodity, props.row.quote_currency, history.page.value)
    // The last item on a page was deleted: step back to a page that has rows.
    if (history.prices.value.length === 0 && history.page.value > 0) {
      await history.load(props.row.commodity, props.row.quote_currency, history.page.value - 1)
    }
  }
  return true
}

async function add(input: PriceInput) {
  const quote = input.quote.trim()
  const elsewhere = props.row !== null && quote !== props.row.quote_currency
  const ok = await apply(addRequest(input), replaced =>
    `${replaced ? 'Price replaced' : 'Price added'}${elsewhere ? ` in ${quote}` : ''}.`)
  if (!ok) return
  addFormRef.value?.reset()
  // The price went to another pair (or this row had no currency yet): show it.
  if (elsewhere && props.row) emit('select', props.row.commodity, quote)
}

function startEdit(p: PricePoint) {
  editError.value = ''
  editing.value = { original: p, date: p.date, number: p.number }
}

async function saveEdit() {
  const e = editing.value
  if (!e || !props.row) return
  const problems = validatePriceInput({
    date: e.date, commodity: props.row.commodity, quote: e.original.quote_currency, number: e.number,
  })
  editError.value = problems.date ?? problems.number ?? ''
  if (editError.value) return
  const request = editRequest(e.original, props.row.commodity, { date: e.date, number: e.number })
  if (!request) {
    editing.value = null
    return
  }
  if (await apply(request, replaced => (replaced ? 'Price moved and replaced the other one.' : 'Price updated.'))) {
    editing.value = null
  }
}

async function confirmDelete() {
  const p = deleting.value
  deleting.value = null
  if (!p || !props.row) return
  await apply(removeRequest(p, props.row.commodity), () => 'Price deleted.')
}
</script>
