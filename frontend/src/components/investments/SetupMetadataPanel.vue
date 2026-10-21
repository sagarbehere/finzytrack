<template>
  <div class="space-y-8">
    <!-- Intro + filter -->
    <div class="sm:flex sm:items-start sm:justify-between sm:gap-6">
      <p class="max-w-2xl text-sm text-gray-500 dark:text-gray-400">
        Optional details for your investments. Each one adds detail to the investment dashboards.
        Nothing here is required.
      </p>
      <label class="mt-4 flex shrink-0 items-center gap-3 sm:mt-0">
        <span class="text-sm font-medium text-gray-900 dark:text-white">Show only items not set</span>
        <div class="group relative inline-flex w-11 shrink-0 rounded-full bg-gray-200 p-0.5 inset-ring inset-ring-gray-900/5 outline-offset-2 outline-indigo-600 transition-colors duration-200 ease-in-out has-checked:bg-indigo-600 has-focus-visible:outline-2 dark:bg-white/5 dark:inset-ring-white/10 dark:outline-indigo-500 dark:has-checked:bg-indigo-500">
          <span class="size-5 rounded-full bg-white shadow-xs ring-1 ring-gray-900/5 transition-transform duration-200 ease-in-out group-has-checked:translate-x-5"></span>
          <input v-model="onlyNotSet" type="checkbox" class="absolute inset-0 size-full appearance-none focus:outline-hidden" aria-label="Show only items not set" />
        </div>
      </label>
    </div>

    <div v-if="isLoading && items.length === 0" class="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
      Loading…
    </div>

    <!-- Commodities -->
    <section v-else>
      <div class="border-b border-gray-200 pb-5 dark:border-white/10">
        <h3 class="text-base font-semibold text-gray-900 dark:text-white">Holdings and currencies</h3>
        <dl class="mt-2 space-y-1 text-sm text-gray-500 dark:text-gray-400">
          <div v-for="f in commodityColumns" :key="f.key">
            <dt class="inline font-medium text-gray-700 dark:text-gray-300">{{ f.label }}:</dt>
            <dd class="ml-1 inline">{{ f.adds }}</dd>
          </div>
        </dl>
      </div>

      <p v-if="visibleCommodities.length === 0" class="mt-4 text-sm text-gray-500 dark:text-gray-400">
        {{ onlyNotSet && commodityItems.length > 0 ? 'Every holding has these details.' : 'No commodities in this ledger yet.' }}
      </p>

      <div v-if="visibleCommodities.length > 0" class="mt-4 overflow-x-auto shadow-sm outline-1 outline-black/5 sm:rounded-lg dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
        <table class="min-w-full divide-y divide-gray-300 dark:divide-white/15">
          <thead class="bg-gray-50 dark:bg-gray-800/75">
            <tr>
              <th scope="col" class="py-3.5 pr-3 pl-4 text-left text-sm font-semibold text-gray-900 sm:pl-6 dark:text-white">Code</th>
              <th
                v-for="f in commodityColumns"
                :key="f.key"
                scope="col"
                :title="f.adds"
                class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900 dark:text-white"
              >
                {{ f.label }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 bg-white dark:divide-white/10 dark:bg-gray-800/50">
            <tr v-for="item in visibleCommodities" :key="item.item">
              <td class="py-3 pr-3 pl-4 text-sm font-medium whitespace-nowrap text-gray-900 sm:pl-6 dark:text-white">
                {{ item.item }}
                <span
                  :class="[
                    'ml-2 inline-flex items-center rounded-md px-2 py-1 text-xs font-medium',
                    item.is_currency
                      ? 'bg-gray-100 text-gray-600 dark:bg-gray-400/10 dark:text-gray-400'
                      : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-400/10 dark:text-indigo-400',
                  ]"
                >
                  {{ item.is_currency ? 'Currency' : 'Holding' }}
                </span>
              </td>
              <td
                v-for="f in commodityColumns"
                :key="f.key"
                :class="['px-3 py-2 text-sm', isEdited(item, f.key) ? 'bg-yellow-50 dark:bg-yellow-500/10' : '']"
              >
                <div
                  v-if="fieldApplies(f, effectiveMetadata(item)) || valueOf(item, f.key)"
                  :class="fieldApplies(f, effectiveMetadata(item)) ? '' : 'opacity-60'"
                  :title="fieldApplies(f, effectiveMetadata(item)) ? undefined : `${notUsedText(f)} Clear it to remove it.`"
                >
                  <ValueCombobox
                    v-if="f.kind === 'enum'"
                    :model-value="valueOf(item, f.key)"
                    :options="f.values ?? []"
                    placeholder="Not set"
                    :aria-label="`${f.label} for ${item.item}`"
                    custom-class="min-w-40"
                    @update:model-value="v => setValue(item, f.key, canonicalValue(f, v))"
                  />
                  <div v-else class="flex items-center gap-2">
                    <input
                      type="text"
                      :value="valueOf(item, f.key)"
                      placeholder="Not set"
                      :aria-label="`${f.label} for ${item.item}`"
                      :inputmode="f.kind === 'integer' ? 'numeric' : undefined"
                      class="block w-full min-w-32 rounded-md bg-white px-3 py-1.5 text-base text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm/6 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                      @input="setValue(item, f.key, ($event.target as HTMLInputElement).value)"
                    />
                    <!-- Indian mutual funds: look up the AMFI scheme code by name -->
                    <button
                      v-if="f.key === 'fetch_symbol' && offersAmfiFinder(item)"
                      type="button"
                      :aria-label="`Find the Indian mutual fund for ${item.item}`"
                      title="Find an Indian mutual fund's scheme code by name"
                      class="shrink-0 rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
                      @click="openAmfiFinder(item)"
                    >
                      Find
                    </button>
                  </div>
                </div>
                <span v-else class="text-gray-400 dark:text-gray-500" :title="`Only for ${f.requires_values?.join(', ')}`">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="hiddenCount(commodityItems) > 0" class="mt-3 text-sm text-gray-500 dark:text-gray-400">
        {{ hiddenCount(commodityItems) }} more not shown.
        <button type="button" class="font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300" @click="onlyNotSet = false">
          Show all
        </button>
      </p>

      <!-- What an asset-class edit does to the currency/holding role -->
      <div
        v-if="roleChanges.length > 0 || operatingNotes.length > 0"
        class="mt-4 rounded-md bg-blue-50 p-4 dark:bg-blue-500/10 dark:outline dark:outline-blue-500/20"
      >
        <div class="flex">
          <InformationCircleIcon class="size-5 shrink-0 text-blue-400" aria-hidden="true" />
          <div class="ml-3 space-y-1 text-sm text-blue-700 dark:text-blue-300">
            <p v-for="c in roleChanges" :key="c.code">
              After saving, {{ c.code }} will count as
              {{ c.role === 'holding' ? 'a holding, not cash,' : 'cash, not a holding,' }}
              in the dashboards.
            </p>
            <p v-if="operatingNotes.length > 0">
              Your operating currencies decide what counts as cash. To count
              {{ operatingNotes.join(', ') }} as cash, add {{ operatingNotes.length === 1 ? 'it' : 'them' }} to the operating currencies in
              <RouterLink to="/settings" class="font-medium underline hover:text-blue-600 dark:hover:text-blue-200">Settings</RouterLink>.
            </p>
          </div>
        </div>
      </div>

      <!-- Save bar -->
      <div v-if="commodityChanges.length > 0" class="mt-4 flex items-center justify-end gap-3">
        <span class="text-sm text-gray-500 dark:text-gray-400">
          {{ commodityChanges.length }} {{ commodityChanges.length === 1 ? 'commodity' : 'commodities' }} changed
        </span>
        <button
          type="button"
          :disabled="isSaving"
          class="rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
          @click="discardAll"
        >
          Discard
        </button>
        <button
          type="button"
          :disabled="isSaving"
          class="rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
          @click="save"
        >
          {{ isSaving ? 'Saving…' : 'Save changes' }}
        </button>
      </div>
    </section>

    <!-- Accounts: cash and deposit accounts, then income accounts. Edited one at a
         time in the account form, which has a typed "Investment details" section. -->
    <section v-for="group in accountGroups" v-show="!isLoading || items.length > 0" :key="group.scope">
      <div class="border-b border-gray-200 pb-5 dark:border-white/10">
        <h3 class="text-base font-semibold text-gray-900 dark:text-white">{{ group.title }}</h3>
        <dl class="mt-2 space-y-1 text-sm text-gray-500 dark:text-gray-400">
          <div v-for="f in group.columns" :key="f.key">
            <dt class="inline font-medium text-gray-700 dark:text-gray-300">{{ f.label }}:</dt>
            <dd class="ml-1 inline">{{ f.adds }}</dd>
          </div>
        </dl>
      </div>

      <p v-if="group.rows.length === 0" class="mt-4 text-sm text-gray-500 dark:text-gray-400">
        {{ onlyNotSet && itemsFor(group.scope).length > 0 ? group.allSetText : group.emptyText }}
      </p>

      <div v-else class="mt-4 overflow-x-auto shadow-sm outline-1 outline-black/5 sm:rounded-lg dark:shadow-none dark:-outline-offset-1 dark:outline-white/10">
        <table class="min-w-full divide-y divide-gray-300 dark:divide-white/15">
          <thead class="bg-gray-50 dark:bg-gray-800/75">
            <tr>
              <th scope="col" class="py-3.5 pr-3 pl-4 text-left text-sm font-semibold text-gray-900 sm:pl-6 dark:text-white">Account</th>
              <th
                v-for="f in group.columns"
                :key="f.key"
                scope="col"
                :title="f.adds"
                class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900 dark:text-white"
              >
                {{ f.label }}
              </th>
              <th scope="col" class="py-3.5 pr-4 pl-3 sm:pr-6"><span class="sr-only">Edit</span></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 bg-white dark:divide-white/10 dark:bg-gray-800/50">
            <tr v-for="item in group.rows" :key="item.item">
              <td class="py-4 pr-3 pl-4 text-sm font-medium whitespace-nowrap text-gray-900 sm:pl-6 dark:text-white">
                {{ item.item }}
                <span
                  v-if="item.closed"
                  class="ml-2 inline-flex items-center rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-600 dark:bg-gray-400/10 dark:text-gray-400"
                >
                  Closed
                </span>
              </td>
              <td v-for="f in group.columns" :key="f.key" class="px-3 py-4 text-sm whitespace-nowrap">
                <template v-if="fieldApplies(f, item.metadata ?? {})">
                  <span v-if="item.metadata?.[f.key]" class="text-gray-900 dark:text-white">{{ item.metadata[f.key] }}</span>
                  <span v-else class="text-gray-400 dark:text-gray-500">Not set</span>
                </template>
                <span
                  v-else-if="item.metadata?.[f.key]"
                  class="text-gray-400 dark:text-gray-500"
                  :title="`${notUsedText(f)} Edit to clear it.`"
                >{{ item.metadata[f.key] }} (not used)</span>
                <span v-else class="text-gray-400 dark:text-gray-500" :title="`Only for ${f.requires_values?.join(', ')}`">—</span>
              </td>
              <td class="py-4 pr-4 pl-3 text-right text-sm font-medium whitespace-nowrap sm:pr-6">
                <button
                  type="button"
                  class="text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300"
                  @click="openAccount(item.item)"
                >
                  Edit<span class="sr-only">, {{ item.item }}</span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="hiddenCount(itemsFor(group.scope)) > 0" class="mt-3 text-sm text-gray-500 dark:text-gray-400">
        {{ hiddenCount(itemsFor(group.scope)) }} more not shown.
        <button type="button" class="font-medium text-indigo-600 hover:text-indigo-900 dark:text-indigo-400 dark:hover:text-indigo-300" @click="onlyNotSet = false">
          Show all
        </button>
      </p>
    </section>

    <AmfiFinderDialog
      v-model:open="amfiOpen"
      :commodity="amfiItem?.item ?? ''"
      :initial-query="amfiItem ? (effectiveMetadata(amfiItem).name || amfiItem.item) : ''"
      @select="useAmfiScheme"
    />

    <AccountFormModal
      v-model:open="accountModalOpen"
      mode="edit"
      :account="editingAccount"
      :investment-scope="editingScope"
      @submit="saveAccount"
    />

    <ConfirmDialog
      :is-open="leaveConfirm.isOpen.value"
      :title="leaveConfirm.dialogOptions.value.title"
      :message="leaveConfirm.dialogOptions.value.message"
      :confirm-text="leaveConfirm.dialogOptions.value.confirmText"
      :cancel-text="leaveConfirm.dialogOptions.value.cancelText"
      :variant="leaveConfirm.dialogOptions.value.variant"
      @confirm="leaveConfirm.handleConfirm"
      @cancel="leaveConfirm.handleCancel"
      @close="leaveConfirm.handleClose"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, RouterLink } from 'vue-router'
import { InformationCircleIcon } from '@heroicons/vue/20/solid'
import type { MetadataFieldSpec, MetadataReviewItem } from '@/services/generated-api'
import type { AccountFormSubmitData, AccountTreeNode } from '@/types/accounts'
import AccountFormModal from '@/components/accounts/AccountFormModal.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import ValueCombobox from '@/components/common/ValueCombobox.vue'
import AmfiFinderDialog from '@/components/investments/AmfiFinderDialog.vue'
import { offersAmfiFinder } from '@/utils/prices'
import { toAccountTreeNode } from '@/composables/accountTreeUtils'
import { useAccounts } from '@/composables/useAccounts'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { itemKey, useMetadataReview } from '@/composables/useMetadataReview'
import { useToast } from '@/composables/useNotifications'
import { buildAccountUpdateRequest } from '@/utils/accountUpdate'
import {
  canonicalValue,
  currencyRoleAfterEdit,
  fieldApplies,
  fieldsFor,
  type MetadataScope,
} from '@/utils/metadataVocabulary'

const {
  fields,
  currencyAssetClasses,
  items,
  summary,
  hasOperatingCurrencies,
  isLoading,
  isSaving,
  load,
  itemsFor,
  valueOf,
  effectiveMetadata,
  setValue,
  isEdited,
  commodityChanges,
  discard,
  saveCommodities,
} = useMetadataReview()
const { success: showSuccessToast } = useToast()

const onlyNotSet = ref(false)

onMounted(async () => {
  await load()
  // Start filtered when something can be added; otherwise show everything.
  onlyNotSet.value = Object.values(summary.value).some(n => n > 0)
})

const commodityItems = computed(() => itemsFor('commodity'))

// Rows stay visible while edited, so a value doesn't vanish mid-edit.
const touched = ref(new Set<string>())
watch(commodityChanges, changes => {
  for (const c of changes) touched.value.add(`commodity|${c.code}`)
})

function isVisible(item: MetadataReviewItem): boolean {
  return !onlyNotSet.value || (item.unset ?? []).length > 0 || touched.value.has(itemKey(item))
}

const visibleCommodities = computed(() => commodityItems.value.filter(isVisible))

// ── Indian mutual funds: find the AMFI scheme code for Price symbol ──
const amfiItem = ref<MetadataReviewItem | null>(null)
const amfiOpen = ref(false)
function openAmfiFinder(item: MetadataReviewItem) {
  amfiItem.value = item
  amfiOpen.value = true
}
function useAmfiScheme(code: string) {
  if (amfiItem.value) setValue(amfiItem.value, 'fetch_symbol', `amfi:${code}`)
}

/** "Not used unless Account type is cd or bond." — labels, not raw keys. */
function notUsedText(f: MetadataFieldSpec): string {
  const requires = fields.value.find(o => o.scope === f.scope && o.key === f.requires_key)
  return `Not used unless ${requires?.label ?? f.requires_key} is ${f.requires_values?.join(' or ')}.`
}

/** How many items of a section the "not set" filter is hiding. */
function hiddenCount(sectionItems: MetadataReviewItem[]): number {
  return onlyNotSet.value ? sectionItems.filter(i => !isVisible(i)).length : 0
}

// Fields that only apply to some rows (I-Bond details) get a column only when a
// visible row needs them or still holds a value for them.
const commodityColumns = computed(() =>
  fieldsFor(fields.value, 'commodity').filter(
    f => !f.requires_key || visibleCommodities.value.some(i => fieldApplies(f, effectiveMetadata(i)) || !!valueOf(i, f.key)),
  ),
)

const roleChanges = computed(() =>
  commodityChanges.value.flatMap(change => {
    if (!('asset-class' in (change.set ?? {})) && !(change.remove ?? []).includes('asset-class')) return []
    const item = commodityItems.value.find(i => i.item === change.code)
    if (!item) return []
    const role = currencyRoleAfterEdit(
      item.is_currency ?? true,
      valueOf(item, 'asset-class'),
      hasOperatingCurrencies.value,
      currencyAssetClasses.value,
    )
    return role ? [{ code: change.code, role }] : []
  }),
)

// ── Accounts ──

const ACCOUNT_GROUPS: { scope: MetadataScope; title: string; emptyText: string; allSetText: string }[] = [
  {
    scope: 'account',
    title: 'Cash and deposit accounts',
    emptyText: 'No cash or deposit accounts in this ledger yet.',
    allSetText: 'Every cash and deposit account has these details.',
  },
  {
    scope: 'income-account',
    title: 'Income accounts',
    emptyText: 'No income accounts in this ledger yet.',
    allSetText: 'Every income account has these details.',
  },
]

const accountGroups = computed(() =>
  ACCOUNT_GROUPS.map(g => {
    const rows = itemsFor(g.scope).filter(i => !onlyNotSet.value || (i.unset ?? []).length > 0)
    const columns = fieldsFor(fields.value, g.scope).filter(
      f => f.review && (!f.requires_key || rows.some(i => fieldApplies(f, i.metadata ?? {}) || !!i.metadata?.[f.key])),
    )
    return { ...g, rows, columns }
  }),
)

const { accountDetails, fetchAccounts, updateAccount } = useAccounts()
const accountModalOpen = ref(false)
const editingAccount = ref<AccountTreeNode | null>(null)
const editingScope = ref<MetadataScope | null>(null)

async function openAccount(name: string) {
  // Fresh read: the save sends a diff against this snapshot.
  await fetchAccounts(true)
  const details = accountDetails.value.find(a => a.name === name)
  if (!details) return
  editingScope.value = (items.value.find(i => i.item === name && i.scope !== 'commodity')?.scope as MetadataScope) ?? null
  editingAccount.value = toAccountTreeNode(details)
  accountModalOpen.value = true
}

async function saveAccount(data: AccountFormSubmitData) {
  const original = editingAccount.value
  if (!original) return
  try {
    await updateAccount(original.fullPath, buildAccountUpdateRequest(original, data))
    accountModalOpen.value = false
    showSuccessToast('Saved', `Updated ${data.name}.`)
    await load()
  } catch {
    // Already shown by useAccounts via errorHandler.display()
  }
}

function discardAll() {
  discard()
  touched.value = new Set()
}

// With operating currencies declared, asset-class doesn't decide cash vs
// holding — tagging a holding-classed commodity cash/currency changes nothing
// until it is added there.
const operatingNotes = computed(() => {
  if (!hasOperatingCurrencies.value) return []
  return commodityChanges.value
    .filter(c => {
      const item = commodityItems.value.find(i => i.item === c.code)
      const ac = (c.set?.['asset-class'] ?? '').toLowerCase()
      return !!item && item.is_currency === false && currencyAssetClasses.value.includes(ac)
    })
    .map(c => c.code)
})

// Unsaved commodity edits survive tab switches; leaving the page asks first.
const leaveConfirm = useConfirmDialog()
onBeforeRouteLeave(async () => {
  if (commodityChanges.value.length === 0) return true
  return await leaveConfirm.showConfirm({
    title: 'Unsaved Changes',
    message: `You have unsaved changes to ${commodityChanges.value.length} ${commodityChanges.value.length === 1 ? 'commodity' : 'commodities'}. They will be lost if you leave this page.`,
    confirmText: 'Leave',
    cancelText: 'Stay',
    variant: 'warning',
  })
})
function beforeUnloadHandler(e: BeforeUnloadEvent) {
  if (commodityChanges.value.length > 0) e.preventDefault()
}
window.addEventListener('beforeunload', beforeUnloadHandler)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnloadHandler))

async function save() {
  const n = commodityChanges.value.length
  if (await saveCommodities()) {
    touched.value = new Set()
    showSuccessToast('Saved', `Updated ${n} ${n === 1 ? 'commodity' : 'commodities'}.`)
  }
}
</script>
