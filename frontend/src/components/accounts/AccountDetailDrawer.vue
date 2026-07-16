<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog as="div" @close="emit('update:open', false)" class="relative z-40">
      <!-- Backdrop -->
      <TransitionChild
        as="template"
        enter="duration-300 ease-out"
        enter-from="opacity-0"
        enter-to="opacity-100"
        leave="duration-200 ease-in"
        leave-from="opacity-100"
        leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/25 dark:bg-black/50" />
      </TransitionChild>

      <!-- Drawer panel -->
      <div class="fixed inset-0 overflow-hidden">
        <div class="absolute inset-0 overflow-hidden">
          <div class="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
            <TransitionChild
              as="template"
              enter="transform transition ease-in-out duration-300"
              enter-from="translate-x-full"
              enter-to="translate-x-0"
              leave="transform transition ease-in-out duration-200"
              leave-from="translate-x-0"
              leave-to="translate-x-full"
            >
              <DialogPanel class="pointer-events-auto w-screen max-w-md">
                <div class="flex h-full flex-col bg-white dark:bg-gray-800 shadow-xl">

                  <!-- Header -->
                  <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10 flex-shrink-0">
                    <div class="flex items-start justify-between gap-4">
                      <div class="flex-1 min-w-0">
                        <DialogTitle class="text-base font-semibold text-gray-900 dark:text-white break-all leading-tight">
                          {{ account?.fullPath }}
                        </DialogTitle>
                        <div class="flex items-center gap-2 mt-1.5">
                          <span
                            class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                            :class="account ? typeColors[account.type] : ''"
                          >
                            {{ account?.type }}
                          </span>
                          <span
                            class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                            :class="account ? statusColors[account.status] : ''"
                          >
                            {{ account?.status === 'open' ? 'Open' : 'Closed' }}
                          </span>
                        </div>
                      </div>
                      <button
                        @click="emit('update:open', false)"
                        class="flex-shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 mt-0.5"
                      >
                        <XMarkIcon class="h-5 w-5" />
                      </button>
                    </div>
                  </div>

                  <!-- Scrollable content -->
                  <div v-if="account" class="flex-1 overflow-y-auto px-6 py-5 space-y-6">

                    <!-- Core Info -->
                    <section>
                      <dl class="grid grid-cols-2 gap-x-6 gap-y-4">
                        <div>
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Opened</dt>
                          <dd class="mt-1 text-sm text-gray-900 dark:text-white">{{ account.openDate || '—' }}</dd>
                        </div>
                        <div>
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Closed</dt>
                          <dd class="mt-1 text-sm text-gray-900 dark:text-white">{{ account.closeDate || '—' }}</dd>
                        </div>
                        <div class="col-span-2">
                          <dt class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1">Currencies</dt>
                          <dd class="flex flex-wrap gap-1">
                            <span
                              v-for="c in account.declaredCurrencies"
                              :key="c"
                              class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
                            >{{ c }}</span>
                            <span v-if="account.declaredCurrencies.length === 0" class="text-sm text-gray-400 dark:text-gray-500">Any</span>
                          </dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Balance -->
                    <section v-if="account.aggregatedBalances.length > 0">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Balance</h3>
                      <div class="space-y-2">
                        <div
                          v-for="bal in account.aggregatedBalances"
                          :key="bal.currency"
                          class="flex justify-between items-center text-sm"
                        >
                          <span class="text-gray-500 dark:text-gray-400">{{ bal.currency }}</span>
                          <span
                            class="font-medium tabular-nums"
                            :class="sign(bal.balance) < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-white'"
                          >
                            {{ toNumber(bal.balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
                          </span>
                        </div>
                      </div>
                    </section>

                    <!-- Budget -->
                    <section>
                      <div class="flex items-center justify-between mb-3">
                        <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Budget</h3>
                        <router-link
                          :to="{ path: '/budgets', query: { account: account.fullPath } }"
                          class="text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
                        >Manage</router-link>
                      </div>
                      <!-- Read-only: the effective budget per currency, with past
                           directives muted below. Editing lives in the Budgets view
                           (the "Manage" link) so this stays a clean summary. -->
                      <div v-if="account && budgetSummaryByCurrency.length > 0" class="space-y-3">
                        <div v-for="grp in budgetSummaryByCurrency" :key="grp.currency" class="text-sm">
                          <div class="flex items-baseline justify-between gap-3">
                            <span v-if="grp.current" class="font-medium text-gray-900 dark:text-white">
                              {{ grp.current.amount }} {{ grp.currency }} · {{ grp.current.interval }}
                            </span>
                            <span v-else class="text-gray-500 dark:text-gray-400">
                              {{ grp.currency }} · ended {{ grp.endedDate }}
                            </span>
                            <span v-if="grp.current" class="shrink-0 text-xs text-gray-400 dark:text-gray-500">since {{ grp.current.date }}</span>
                          </div>
                          <div
                            v-for="p in grp.past"
                            :key="p.id"
                            class="mt-0.5 flex items-baseline justify-between gap-3 text-xs text-gray-400 dark:text-gray-500"
                          >
                            <span>{{ p.ended ? 'ended' : `${p.amount} ${grp.currency} · ${p.interval}` }}</span>
                            <span class="shrink-0">{{ p.date }}</span>
                          </div>
                        </div>
                      </div>
                      <p v-else class="text-sm text-gray-400 dark:text-gray-500">
                        No budget set. <router-link :to="{ path: '/budgets', query: { account: account?.fullPath } }" class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400">Add one</router-link>.
                      </p>
                    </section>

                    <!-- Banking Details -->
                    <section v-if="hasBankingDetails">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Banking Details</h3>
                      <dl class="space-y-2">
                        <div v-if="account.metadata['account_number']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">Account Number</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['account_number'] }}</dd>
                        </div>
                        <div v-if="account.metadata['ifsc_code']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">IFSC Code</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['ifsc_code'] }}</dd>
                        </div>
                        <div v-if="account.metadata['swift_bic']" class="flex justify-between items-center gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">SWIFT / BIC</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white font-mono text-right">{{ account.metadata['swift_bic'] }}</dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Custom Fields -->
                    <section v-if="customFields.length > 0">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Custom Fields</h3>
                      <dl class="space-y-2">
                        <div v-for="field in customFields" :key="field.key" class="flex justify-between items-start gap-4">
                          <dt class="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">{{ formatFieldKey(field.key) }}</dt>
                          <dd class="text-sm font-medium text-gray-900 dark:text-white text-right">{{ field.value }}</dd>
                        </div>
                      </dl>
                    </section>

                    <!-- Notes -->
                    <section v-if="account.notes">
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Notes</h3>
                      <p class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ account.notes }}</p>
                    </section>

                    <!-- Documents -->
                    <section>
                      <h3 class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">Documents</h3>
                      <ul v-if="documents.length > 0" class="divide-y divide-gray-100 dark:divide-white/5 mb-3">
                        <li
                          v-for="doc in documents"
                          :key="doc.path"
                          class="flex items-center justify-between gap-3 py-2"
                        >
                          <button
                            type="button"
                            @click="openDocument(doc.path, doc.display_name)"
                            class="flex items-center gap-2 min-w-0 text-left text-sm text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300"
                          >
                            <PaperClipIcon class="h-4 w-4 flex-shrink-0" />
                            <span class="truncate">{{ doc.display_name }}</span>
                          </button>
                          <button
                            type="button"
                            @click="detach(doc.path)"
                            class="flex-shrink-0 text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                            title="Detach document"
                          >
                            <XMarkIcon class="h-4 w-4" />
                          </button>
                        </li>
                      </ul>
                      <p v-else-if="!documentsLoading" class="text-sm text-gray-400 dark:text-gray-500 italic mb-3">
                        No documents attached yet.
                      </p>
                      <DocumentUploadZone :uploading="isUploading" @files-selected="onFiles" />
                    </section>

                    <!-- Empty metadata state -->
                    <p
                      v-if="!hasBankingDetails && !customFields.length && !account.notes"
                      class="text-sm text-gray-400 dark:text-gray-500 italic"
                    >
                      No additional details. Click Edit to add metadata.
                    </p>

                  </div>

                  <!-- Footer -->
                  <div class="px-6 py-4 border-t border-gray-200 dark:border-white/10 flex-shrink-0">
                    <button
                      @click="account && emit('edit', account)"
                      class="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
                    >
                      Edit Account
                    </button>
                  </div>

                </div>
              </DialogPanel>
            </TransitionChild>
          </div>
        </div>
      </div>

      <!-- Nested so HeadlessUI suppresses this drawer's Esc/outside-click while
           a document preview is open. -->
      <DocumentPreviewModal />
    </Dialog>
  </TransitionRoot>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from '@headlessui/vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import { PaperClipIcon } from '@heroicons/vue/20/solid'
import DocumentUploadZone from '@/components/documents/DocumentUploadZone.vue'
import DocumentPreviewModal from '@/components/documents/DocumentPreviewModal.vue'
import { useDocuments } from '@/composables/useDocuments'
import { useBudgets } from '@/composables/useBudgets'
import type { DocumentDetails, BudgetItem } from '@/services/generated-api'
import type { AccountTreeNode } from '@/types/accounts'
import { typeColors, statusColors } from '@/types/accounts'
import { sign, toNumber } from '@/utils/money'
import { todayLocal } from '@/utils/date'

const BANKING_KEYS = ['account_number', 'ifsc_code', 'swift_bic']
const RESERVED_KEYS = new Set(['description', ...BANKING_KEYS])

interface Props {
  open: boolean
  account: AccountTreeNode | null
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'edit', account: AccountTreeNode): void
  /** Emitted after an account document is attached/detached, so callers can refresh badge counts. */
  (e: 'documents-changed', account: string): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const {
  uploadDocument, openDocument, listAccountDocuments,
  attachAccountDocument, detachAccountDocument, isUploading,
} = useDocuments()

const documents = ref<DocumentDetails[]>([])
const documentsLoading = ref(false)

async function refreshDocuments() {
  if (!props.account) { documents.value = []; return }
  documentsLoading.value = true
  try {
    documents.value = await listAccountDocuments(props.account.fullPath)
  } catch {
    documents.value = []
  } finally {
    documentsLoading.value = false
  }
}

// Full budget-directive history for this account, shown read-only (grouped by
// currency: the effective budget today + past directives). Editing is deferred
// to the Budgets view via the "Manage" link (§7.1), so the drawer stays a clean
// summary and doesn't duplicate the editor.
const { fetch: fetchBudgets } = useBudgets()
const accountBudgets = ref<BudgetItem[]>([])
async function refreshBudgets() {
  if (!props.account) { accountBudgets.value = []; return }
  try {
    accountBudgets.value = await fetchBudgets({ history: true, account: props.account.fullPath })
  } catch {
    accountBudgets.value = []
  }
}

// Per currency: the effective directive as of today (`current`, or `endedDate`
// when it's a "budget end" tombstone) and the remaining directives (`past`),
// newest first.
type BudgetGroup = { currency: string; current: BudgetItem | null; endedDate: string | null; past: BudgetItem[] }
const budgetSummaryByCurrency = computed<BudgetGroup[]>(() => {
  const today = todayLocal()
  const byCurrency = new Map<string, BudgetItem[]>()
  for (const b of accountBudgets.value) {
    const list = byCurrency.get(b.currency)
    if (list) list.push(b)
    else byCurrency.set(b.currency, [b])
  }
  return [...byCurrency.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([currency, directives]) => {
      // Newest first; same-day ties resolve by source order (last wins, §4.3).
      const sorted = [...directives].sort(
        (a, b) => b.date.localeCompare(a.date) || (b.source_lineno ?? 0) - (a.source_lineno ?? 0),
      )
      const effective = sorted.find((d) => d.date <= today) ?? null
      const ended = !!effective?.ended
      return {
        currency,
        current: effective && !ended ? effective : null,
        endedDate: ended ? effective!.date : null,
        past: sorted.filter((d) => d !== effective),
      }
    })
})

// Fetch documents + budgets when the drawer opens or switches account.
watch(
  () => [props.open, props.account?.fullPath] as const,
  ([open]) => { if (open) { refreshDocuments(); refreshBudgets() } },
  { immediate: true },
)


async function onFiles(files: File[]) {
  if (!props.account) return
  const account = props.account.fullPath
  for (const file of files) {
    const stored = await uploadDocument(file, { date: todayLocal(), narration: props.account.name })
    await attachAccountDocument({ account, date: todayLocal(), path: stored.path })
  }
  await refreshDocuments()
  emit('documents-changed', account)
}

async function detach(path: string) {
  if (!props.account) return
  const account = props.account.fullPath
  await detachAccountDocument(account, path)
  await refreshDocuments()
  emit('documents-changed', account)
}

const hasBankingDetails = computed(() =>
  BANKING_KEYS.some(k => props.account?.metadata[k])
)

const customFields = computed(() => {
  if (!props.account) return []
  return Object.entries(props.account.metadata)
    .filter(([k]) => !RESERVED_KEYS.has(k))
    .map(([key, value]) => ({ key, value }))
})

function formatFieldKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
</script>
