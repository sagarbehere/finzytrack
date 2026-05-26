<template>
  <div class="card-scroll-container rounded-lg ring-1 ring-gray-300 bg-gray-100 dark:ring-white/10 dark:bg-gray-950/50">
    <div class="grid grid-cols-1 gap-3 p-3">
      <div
        v-for="(transaction, txIdx) in transactions"
        :key="transaction.id"
        class="overflow-hidden rounded-lg bg-white shadow ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10"
      >
        <!-- Transaction-level fields -->
        <div class="divide-y divide-gray-100 dark:divide-white/5">
          <!-- Status -->
          <div v-if="isColumnVisible('status')" class="px-4 py-2">
            <TransactionStatusIndicator
              :transaction="transaction"
              :import-context="importContext?.get(transaction.id)"
              :ledger-context="ledgerContext?.get(transaction.id)"
              @duplicate-click="(id: string) => emit('duplicateClick', id)"
            />
          </div>

          <!-- Index -->
          <div v-if="isColumnVisible('index')" class="flex items-center justify-between px-4 py-2">
            <span class="text-xs font-medium text-gray-500 dark:text-gray-400">#</span>
            <span class="text-sm text-gray-900 dark:text-white">{{ txIdx + 1 }}</span>
          </div>

          <!-- Date -->
          <div v-if="isColumnVisible('date')" class="flex items-center justify-between px-4 py-2">
            <span class="text-xs font-medium text-gray-500 dark:text-gray-400">Date</span>
            <input
              v-if="editable"
              type="date"
              :value="transaction.date"
              @input="(e) => emit('updateField', transaction.id, 'date', (e.target as HTMLInputElement).value)"
              :class="inputClasses('text-right max-w-[10rem]')"
              autocomplete="off"
            />
            <span v-else class="text-sm text-gray-900 dark:text-white">{{ transaction.date }}</span>
          </div>

          <!-- Flag -->
          <div v-if="isColumnVisible('flag')" class="flex items-center justify-between px-4 py-2">
            <span class="text-xs font-medium text-gray-500 dark:text-gray-400">Flag</span>
            <input
              v-if="editable"
              type="text"
              :value="transaction.flag"
              @change="(e) => emit('updateField', transaction.id, 'flag', (e.target as HTMLInputElement).value)"
              :class="inputClasses('text-center max-w-[3rem]')"
              autocomplete="off"
            />
            <span v-else class="text-sm text-gray-900 dark:text-white">{{ transaction.flag }}</span>
          </div>

          <!-- Payee -->
          <div v-if="isColumnVisible('payee')" class="px-4 py-2">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Payee</label>
            <input
              v-if="editable"
              type="text"
              :value="transaction.payee"
              @input="(e) => emit('updateField', transaction.id, 'payee', (e.target as HTMLInputElement).value)"
              placeholder="Payee"
              :class="inputClasses()"
              autocomplete="off"
            />
            <span v-else class="text-sm text-gray-900 dark:text-white">{{ transaction.payee || '—' }}</span>
          </div>

          <!-- Memo -->
          <div v-if="isColumnVisible('memo')" class="px-4 py-2">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Memo</label>
            <input
              v-if="editable"
              type="text"
              :value="transaction.memo || ''"
              @input="(e) => emit('updateField', transaction.id, 'memo', (e.target as HTMLInputElement).value)"
              placeholder="Memo"
              :class="inputClasses()"
              autocomplete="off"
            />
            <span v-else class="text-sm text-gray-900 dark:text-white">{{ transaction.memo || '—' }}</span>
          </div>

          <!-- Narration -->
          <div v-if="isColumnVisible('narration')" class="px-4 py-2">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Narration</label>
            <input
              v-if="editable"
              type="text"
              :value="transaction.narration"
              @input="(e) => emit('updateField', transaction.id, 'narration', (e.target as HTMLInputElement).value)"
              placeholder="Description"
              :class="inputClasses()"
              autocomplete="off"
            />
            <span v-else class="text-sm text-gray-900 dark:text-white">{{ transaction.narration || '—' }}</span>
          </div>

          <!-- Tags/Links -->
          <div v-if="isColumnVisible('tags_links')" class="px-4 py-2">
            <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Tags / Links</label>
            <input
              v-if="editable"
              type="text"
              :value="[...transaction.tags.map(t => `#${t}`), ...transaction.links.map(l => `^${l}`)].join(' ')"
              @input="(e) => emit('updateField', transaction.id, 'tags_links', (e.target as HTMLInputElement).value)"
              placeholder="#tag ^link"
              :class="inputClasses()"
              autocomplete="off"
            />
            <div v-else-if="transaction.tags.length > 0 || transaction.links.length > 0" class="flex flex-wrap gap-1">
              <span
                v-for="tag in transaction.tags"
                :key="tag"
                class="inline-flex items-center rounded-md bg-indigo-50 px-1.5 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20 dark:bg-indigo-500/10 dark:text-indigo-400 dark:ring-indigo-500/20"
              >#{{ tag }}</span>
              <span
                v-for="link in transaction.links"
                :key="link"
                class="inline-flex items-center rounded-md bg-gray-50 px-1.5 py-0.5 text-xs font-medium text-gray-700 ring-1 ring-inset ring-gray-600/20 dark:bg-gray-500/10 dark:text-gray-400 dark:ring-gray-500/20"
              >^{{ link }}</span>
            </div>
            <span v-else class="text-sm text-gray-400 dark:text-gray-500">—</span>
          </div>
        </div>

        <!-- Postings -->
        <div
          v-for="(posting, idx) in transaction.postings"
          :key="idx"
          class="border-t border-gray-200 dark:border-white/10"
        >
          <div class="flex items-center justify-between px-4 pt-2 pb-1">
            <span class="inline-flex items-center rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-600/20 dark:bg-indigo-500/10 dark:text-indigo-400 dark:ring-indigo-500/20">Posting {{ idx + 1 }}</span>
            <button
              v-if="editable && isColumnVisible('actions')"
              @click="emit('removePosting', transaction.id, idx)"
              class="inline-flex items-center justify-center w-5 h-5 text-red-600 hover:text-red-800 hover:bg-red-50 rounded text-xs dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20"
              title="Remove posting"
            >&times;</button>
          </div>
          <div class="divide-y divide-gray-100 dark:divide-white/5">
            <!-- Account -->
            <div v-if="isColumnVisible('account')" class="px-4 py-2">
              <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Account</label>
              <AccountDropdown
                v-if="editable"
                :model-value="posting.account"
                @update:model-value="(val: string) => emit('updateField', transaction.id, `postings.${idx}.account`, val)"
                :allow-custom="false"
                placeholder="Select account..."
                custom-class="!text-sm !py-1.5"
              />
              <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.account || '—' }}</span>
            </div>

            <!-- Amount -->
            <div v-if="isColumnVisible('amount')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Amount</label>
              <input
                v-if="editable"
                v-bind="numericInputProps(
                  transaction.id, idx, 'amount',
                  posting.amount,
                  (raw: string) => emit('updateField', transaction.id, `postings.${idx}.amount`, raw === '' ? null : toMoney(raw)),
                  `${inputClasses()} text-right max-w-[8rem] ${getAmountColorClass(posting.amount)}`
                )"
              />
              <span v-else class="text-sm tabular-nums font-medium" :class="getAmountColorClass(posting.amount)">
                {{ posting.amount !== null ? toFixed(posting.amount, 2) : '—' }}
              </span>
            </div>

            <!-- Currency -->
            <div v-if="isColumnVisible('currency')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Currency</label>
              <div class="max-w-[8rem]">
                <CommodityDropdown
                  v-if="editable"
                  :model-value="posting.currency"
                  @update:model-value="(val: string) => emit('updateField', transaction.id, `postings.${idx}.currency`, val)"
                  :allow-custom="false"
                  :show-details="false"
                  placeholder="CURR"
                  custom-class="!text-sm !py-1"
                />
                <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.currency || '—' }}</span>
              </div>
            </div>

            <!-- Cost Amount -->
            <div v-if="isColumnVisible('cost_amount')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Cost Amount</label>
              <input
                v-if="editable"
                v-bind="numericInputProps(
                  transaction.id, idx, 'cost.amount',
                  posting.cost?.amount,
                  (raw: string) => emit('updateField', transaction.id, `postings.${idx}.cost.amount`, raw === '' ? null : toMoney(raw)),
                  `${inputClasses()} text-right max-w-[8rem]`
                )"
              />
              <span v-else class="text-sm tabular-nums text-gray-900 dark:text-white">
                {{ posting.cost?.amount !== undefined ? toFixed(posting.cost.amount, 2) : '—' }}
              </span>
            </div>

            <!-- Cost Currency -->
            <div v-if="isColumnVisible('cost_currency')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Cost Currency</label>
              <div class="max-w-[8rem]">
                <CommodityDropdown
                  v-if="editable"
                  :model-value="posting.cost?.currency || ''"
                  @update:model-value="(val: string) => emit('updateField', transaction.id, `postings.${idx}.cost.currency`, val)"
                  :allow-custom="false"
                  :show-details="false"
                  :clearable="true"
                  placeholder="CURR"
                  custom-class="!text-sm !py-1"
                />
                <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.cost?.currency || '—' }}</span>
              </div>
            </div>

            <!-- Cost Date -->
            <div v-if="isColumnVisible('cost_date')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Cost Date</label>
              <input
                v-if="editable"
                type="date"
                :value="posting.cost?.date || ''"
                @input="(e) => emit('updateField', transaction.id, `postings.${idx}.cost.date`, (e.target as HTMLInputElement).value)"
                :class="inputClasses('text-right max-w-[10rem]')"
                autocomplete="off"
              />
              <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.cost?.date || '—' }}</span>
            </div>

            <!-- Price Amount -->
            <div v-if="isColumnVisible('price_amount')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Price Amount</label>
              <input
                v-if="editable"
                v-bind="numericInputProps(
                  transaction.id, idx, 'price.amount',
                  posting.price?.amount,
                  (raw: string) => emit('updateField', transaction.id, `postings.${idx}.price.amount`, raw === '' ? null : toMoney(raw)),
                  `${inputClasses()} text-right max-w-[8rem]`
                )"
              />
              <span v-else class="text-sm tabular-nums text-gray-900 dark:text-white">
                {{ posting.price?.amount !== undefined ? toFixed(posting.price.amount, 2) : '—' }}
              </span>
            </div>

            <!-- Price Currency -->
            <div v-if="isColumnVisible('price_currency')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Price Currency</label>
              <div class="max-w-[8rem]">
                <CommodityDropdown
                  v-if="editable"
                  :model-value="posting.price?.currency || ''"
                  @update:model-value="(val: string) => emit('updateField', transaction.id, `postings.${idx}.price.currency`, val)"
                  :allow-custom="false"
                  :show-details="false"
                  :clearable="true"
                  placeholder="CURR"
                  custom-class="!text-sm !py-1"
                />
                <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.price?.currency || '—' }}</span>
              </div>
            </div>

            <!-- Price Type -->
            <div v-if="isColumnVisible('price_type')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Price Type</label>
              <div class="max-w-[8rem]">
                <PriceTypeDropdown
                  v-if="editable"
                  :model-value="posting.price?.type || ''"
                  @update:model-value="(val: string) => emit('updateField', transaction.id, `postings.${idx}.price.type`, val)"
                  placeholder="Type"
                  custom-class="!text-sm !py-1"
                />
                <span v-else class="text-sm text-gray-900 dark:text-white">{{ posting.price?.type || '—' }}</span>
              </div>
            </div>

            <!-- Balance (read-only) -->
            <div v-if="isColumnVisible('balance')" class="flex items-center justify-between px-4 py-2">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400">Balance</label>
              <span v-if="ledgerContext?.get(transaction.id)?.balance !== undefined" class="text-sm tabular-nums font-mono text-gray-900 dark:text-white">
                {{ toFixed(ledgerContext!.get(transaction.id)!.balance!, 2) }}
              </span>
              <span v-else class="text-sm text-gray-400">—</span>
            </div>
          </div>
        </div>

        <!-- Actions (add/remove posting, delete transaction) -->
        <div v-if="editable && isColumnVisible('actions')" class="flex items-center justify-end gap-2 border-t border-gray-200 px-4 py-2 dark:border-white/10">
          <button
            @click="emit('addPosting', transaction.id)"
            class="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-900/20"
          >+ Add posting</button>
          <button
            @click="emit('removeTransaction', transaction)"
            class="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20"
          >Remove</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import PriceTypeDropdown from '@/components/common/PriceTypeDropdown.vue'
import TransactionStatusIndicator from '@/components/common/TransactionStatusIndicator.vue'
import type { TransactionViewModel, ImportContext, LedgerContext } from '@/types/transactions'
import { sign, toFixed, toMoney, type Money } from '@/utils/money'

interface Props {
  transactions: TransactionViewModel[]
  columnVisibility: Record<string, boolean>
  editable?: boolean
  importContext?: Map<string, ImportContext>
  ledgerContext?: Map<string, LedgerContext>
}

const props = withDefaults(defineProps<Props>(), {
  editable: false,
})

const emit = defineEmits<{
  (e: 'updateField', txId: string, path: string, value: unknown): void
  (e: 'addPosting', txId: string): void
  (e: 'removePosting', txId: string, postingIndex: number): void
  (e: 'removeTransaction', transaction: TransactionViewModel): void
  (e: 'duplicateClick', transactionId: string): void
}>()

const isColumnVisible = (columnId: string) => props.columnVisibility[columnId] === true

const inputClasses = (extraClasses = '') => {
  return `w-full min-w-0 rounded-md border-0 bg-white py-1.5 px-3 text-sm text-gray-900 outline-0 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:placeholder:text-gray-500 dark:focus:outline-indigo-500 ${extraClasses}`
}

/** Returns color classes for a monetary amount */
const getAmountColorClass = (amount: Money | null | undefined): string => {
  if (amount == null) return 'text-gray-700 dark:text-gray-300'
  const s = sign(amount)
  if (s > 0) return 'text-green-700 dark:text-green-400'
  if (s < 0) return 'text-red-700 dark:text-red-400'
  return 'text-gray-700 dark:text-gray-300'
}

// Track raw input strings for numeric fields to preserve trailing dots/zeros during editing.
const rawAmountStrings = ref<Record<string, string>>({})

const numericInputProps = (
  txId: string, postingIdx: number, field: string,
  currentValue: Money | null | undefined,
  updateFn: (raw: string) => void,
  extraClasses: string = ''
): Record<string, unknown> => {
  const key = `${txId}-${postingIdx}-${field}`
  const rawStr = rawAmountStrings.value[key]
  const fallback = (() => {
    if (currentValue === null || currentValue === undefined) return ''
    const s = String(currentValue)
    const decimals = s.includes('.') ? s.split('.')[1].length : 0
    return decimals < 2 ? toFixed(currentValue, 2) : s
  })()
  return {
    type: 'text',
    inputmode: 'decimal',
    value: rawStr !== undefined ? rawStr : fallback,
    onInput: (e: any) => {
      const raw: string = e.target.value
      if (raw !== '' && !/^-?\d*\.?\d*$/.test(raw)) {
        e.target.value = rawAmountStrings.value[key] ?? fallback
        return
      }
      rawAmountStrings.value[key] = raw
      updateFn(raw)
    },
    onBlur: () => { delete rawAmountStrings.value[key] },
    class: extraClasses,
    autocomplete: 'off'
  }
}
</script>

<style scoped>
.card-scroll-container {
  overflow-y: auto;
  max-height: 600px;
  will-change: transform; /* Promote to GPU compositing layer for smooth scroll */
}
</style>
