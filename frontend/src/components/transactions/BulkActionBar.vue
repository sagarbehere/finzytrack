<template>
  <div
    class="mb-4 flex flex-wrap items-center gap-3 rounded-lg bg-indigo-50 px-4 py-3 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/20"
  >
    <span class="text-sm font-semibold text-indigo-900 dark:text-indigo-200">
      {{ selectedCount }} selected
    </span>

    <div class="flex flex-wrap items-center gap-2">
      <!-- Autocategorize (direct action, not a popover): resolves Expenses:Unknown -->
      <button
        :class="[triggerClass, 'disabled:opacity-50 disabled:cursor-not-allowed']"
        :disabled="categorizing"
        @click="emit('autocategorize')"
      >
        {{ categorizing ? 'Categorizing…' : 'Autocategorize' }}
      </button>

      <!-- Replace account -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Replace account</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Replace account</p>
          <label :class="fieldLabelClass">From (accounts in selection)</label>
          <SelectListbox v-model="replaceFrom" :options="accountsInSelection" placeholder="Select account…" />
          <label :class="fieldLabelClass">To</label>
          <AccountDropdown v-model="replaceTo" :allow-custom="false" placeholder="Account…" />
          <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">Only postings matching “From” change.</p>
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button
              :class="applyClass"
              :disabled="!replaceFrom || !replaceTo || replaceFrom === replaceTo"
              @click="emitReplaceAccount(close)"
            >Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Tag -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Tag</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Tag</p>
          <div :class="modeRowClass">
            <label :class="radioLabelClass"><input type="radio" value="add" v-model="tagMode" /> Add</label>
            <label :class="radioLabelClass"><input type="radio" value="remove" v-model="tagMode" /> Remove</label>
          </div>
          <input v-if="tagMode === 'add'" v-model="tagValue" type="text" placeholder="tag" :class="inputClass" @keydown.enter.prevent="tagValue.trim() && emitTag(close)" />
          <SelectListbox v-else v-model="tagValue" :options="tagsInSelection" placeholder="Select tag…" />
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button :class="applyClass" :disabled="!tagValue.trim()" @click="emitTag(close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Link -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Link</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Link</p>
          <div :class="modeRowClass">
            <label :class="radioLabelClass"><input type="radio" value="add" v-model="linkMode" /> Add</label>
            <label :class="radioLabelClass"><input type="radio" value="remove" v-model="linkMode" /> Remove</label>
          </div>
          <input v-if="linkMode === 'add'" v-model="linkValue" type="text" placeholder="link" :class="inputClass" @keydown.enter.prevent="linkValue.trim() && emitLink(close)" />
          <SelectListbox v-else v-model="linkValue" :options="linksInSelection" placeholder="Select link…" />
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button :class="applyClass" :disabled="!linkValue.trim()" @click="emitLink(close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Flag -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Flag</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Set flag</p>
          <div class="flex gap-2">
            <button :class="applyClass" @click="emitFlag('*', close)">Reconciled (*)</button>
            <button :class="applyClass" @click="emitFlag('!', close)">Pending (!)</button>
          </div>
          <label :class="fieldLabelClass">Or a custom flag (any single character)</label>
          <div class="flex gap-2">
            <input v-model="customFlag" type="text" maxlength="1" placeholder="e.g. P" :class="`${inputClass} w-16`" />
            <button :class="applyClass" :disabled="!customFlag.trim()" @click="emitFlag(customFlag.trim(), close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Payee -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Payee</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Payee</p>
          <div :class="modeRowClass">
            <label :class="radioLabelClass"><input type="radio" value="set" v-model="payeeMode" /> Set</label>
            <label :class="radioLabelClass"><input type="radio" value="append" v-model="payeeMode" /> Append</label>
          </div>
          <input v-model="payeeValue" type="text" :placeholder="payeeMode === 'set' ? 'New payee' : 'Text to append'" :class="inputClass" @keydown.enter.prevent="payeeValue.trim() && emitPayee(close)" />
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button :class="applyClass" :disabled="!payeeValue.trim()" @click="emitPayee(close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Narration -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Narration</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Narration</p>
          <div :class="modeRowClass">
            <label :class="radioLabelClass"><input type="radio" value="set" v-model="narrationMode" /> Set</label>
            <label :class="radioLabelClass"><input type="radio" value="append" v-model="narrationMode" /> Append</label>
          </div>
          <input v-model="narrationValue" type="text" :placeholder="narrationMode === 'set' ? 'New narration' : 'Text to append'" :class="inputClass" @keydown.enter.prevent="narrationValue.trim() && emitNarration(close)" />
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button :class="applyClass" :disabled="!narrationValue.trim()" @click="emitNarration(close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>

      <!-- Metadata -->
      <Popover class="relative">
        <PopoverButton :class="triggerClass">Metadata</PopoverButton>
        <PopoverPanel v-slot="{ close }" :class="panelClass">
          <p :class="panelTitleClass">Metadata</p>
          <div :class="modeRowClass">
            <label :class="radioLabelClass"><input type="radio" value="set" v-model="metaMode" /> Set</label>
            <label :class="radioLabelClass"><input type="radio" value="remove" v-model="metaMode" /> Remove</label>
            <label :class="radioLabelClass"><input type="radio" value="rename" v-model="metaMode" /> Rename</label>
          </div>

          <template v-if="metaMode === 'set'">
            <input v-model="metaKey" type="text" placeholder="key" :class="inputClass" />
            <input v-model="metaValue" type="text" placeholder="value" :class="`${inputClass} mt-2`" />
          </template>
          <template v-else-if="metaMode === 'remove'">
            <SelectListbox v-model="metaKey" :options="editableMetaKeysInSelection" placeholder="Select key…" />
          </template>
          <template v-else>
            <SelectListbox v-model="metaKey" :options="editableMetaKeysInSelection" placeholder="From key…" />
            <input v-model="metaRenameTo" type="text" placeholder="to key" :class="`${inputClass} mt-2`" />
          </template>

          <p v-if="metaError" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ metaError }}</p>
          <div :class="panelActionsClass">
            <button :class="cancelClass" @click="close()">Cancel</button>
            <button :class="applyClass" :disabled="!metaValid" @click="emitMeta(close)">Apply</button>
          </div>
        </PopoverPanel>
      </Popover>
    </div>

    <!-- Delete is immediate (not a staged operation), so it sits apart with a
         danger style and its own confirm (handled by the parent). -->
    <button
      class="ml-auto rounded-md bg-red-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-red-500 dark:bg-red-500 dark:hover:bg-red-400"
      @click="emit('delete')"
    >
      Delete
    </button>

    <button
      class="text-sm font-medium text-indigo-700 hover:text-indigo-900 dark:text-indigo-300 dark:hover:text-indigo-100"
      @click="emit('clear')"
    >
      Clear selection
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/vue'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import SelectListbox from '@/components/common/SelectListbox.vue'
import { isValidMetaKey, isEditableMetaKey, type BulkOperation } from '@/utils/bulkOperations'

const props = defineProps<{
  selectedCount: number
  accountsInSelection: string[]
  /** Editable metadata keys present across the selection (for remove/rename). */
  editableMetaKeysInSelection: string[]
  /** Tags / links present across the selection (for remove). */
  tagsInSelection: string[]
  linksInSelection: string[]
  /** True while an autocategorize request is in flight (disables the button). */
  categorizing?: boolean
}>()

const emit = defineEmits<{
  (e: 'apply', op: BulkOperation): void
  (e: 'delete'): void
  (e: 'autocategorize'): void
  (e: 'clear'): void
}>()

// Shared class strings (Tailwind Plus style system).
const triggerClass = 'rounded-md bg-white px-2.5 py-1.5 text-sm font-medium text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:inset-ring-white/10 dark:hover:bg-white/20'
const panelClass = 'absolute left-0 z-30 mt-2 w-64 rounded-lg bg-white p-4 shadow-xl ring-1 ring-gray-200 dark:bg-gray-800 dark:ring-white/10'
const panelTitleClass = 'mb-2 text-sm font-semibold text-gray-900 dark:text-white'
const fieldLabelClass = 'mt-3 mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400'
const inputClass = 'block w-full rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10'
const modeRowClass = 'mb-2 flex gap-3'
const radioLabelClass = 'flex items-center gap-1 text-sm text-gray-700 dark:text-gray-300'
const panelActionsClass = 'mt-3 flex justify-end gap-2'
const cancelClass = 'rounded-md px-2.5 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-white/10'
const applyClass = 'rounded-md bg-indigo-600 px-2.5 py-1.5 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:hover:bg-indigo-400'

// ── Replace account ──────────────────────────────────────────────────────────
const replaceFrom = ref('')
const replaceTo = ref('')
function emitReplaceAccount(close: () => void) {
  if (!replaceFrom.value || !replaceTo.value || replaceFrom.value === replaceTo.value) return
  emit('apply', { type: 'replaceAccount', from: replaceFrom.value, to: replaceTo.value })
  replaceFrom.value = ''
  replaceTo.value = ''
  close()
}

// ── Tag / Link ───────────────────────────────────────────────────────────────
const tagMode = ref<'add' | 'remove'>('add')
const tagValue = ref('')
// Clear the value when flipping Add/Remove so a typed tag doesn't linger in the
// remove dropdown (and vice versa).
watch(tagMode, () => { tagValue.value = '' })
function emitTag(close: () => void) {
  const tag = tagValue.value.trim()
  if (!tag) return
  emit('apply', tagMode.value === 'add' ? { type: 'addTag', tag } : { type: 'removeTag', tag })
  tagValue.value = ''
  close()
}

const linkMode = ref<'add' | 'remove'>('add')
const linkValue = ref('')
watch(linkMode, () => { linkValue.value = '' })
function emitLink(close: () => void) {
  const link = linkValue.value.trim()
  if (!link) return
  emit('apply', linkMode.value === 'add' ? { type: 'addLink', link } : { type: 'removeLink', link })
  linkValue.value = ''
  close()
}

// ── Flag ─────────────────────────────────────────────────────────────────────
// Beancount accepts any single character as a flag; * and ! are the common ones.
const customFlag = ref('')
function emitFlag(flag: string, close: () => void) {
  if (!flag) return
  emit('apply', { type: 'setFlag', flag })
  customFlag.value = ''
  close()
}

// ── Payee (set / append) ─────────────────────────────────────────────────────
const payeeMode = ref<'set' | 'append'>('set')
const payeeValue = ref('')
function emitPayee(close: () => void) {
  const text = payeeValue.value.trim()
  if (!text) return
  emit('apply', payeeMode.value === 'set' ? { type: 'setPayee', payee: text } : { type: 'appendPayee', text })
  payeeValue.value = ''
  close()
}

// ── Narration (set / append) ─────────────────────────────────────────────────
const narrationMode = ref<'set' | 'append'>('set')
const narrationValue = ref('')
function emitNarration(close: () => void) {
  const text = narrationValue.value.trim()
  if (!text) return
  emit('apply', narrationMode.value === 'set' ? { type: 'setNarration', narration: text } : { type: 'appendNarration', text })
  narrationValue.value = ''
  close()
}

// ── Metadata ─────────────────────────────────────────────────────────────────
const metaMode = ref<'set' | 'remove' | 'rename'>('set')
const metaKey = ref('')
const metaValue = ref('')
const metaRenameTo = ref('')

const metaError = computed(() => {
  if (metaMode.value === 'set') {
    if (metaKey.value && !isValidMetaKey(metaKey.value)) return 'Invalid key (lowercase letters, digits, - _)'
    if (metaKey.value && !isEditableMetaKey(metaKey.value)) return 'That key is system-managed'
  }
  if (metaMode.value === 'rename') {
    if (metaRenameTo.value && !isValidMetaKey(metaRenameTo.value)) return 'Invalid target key'
    if (metaRenameTo.value && !isEditableMetaKey(metaRenameTo.value)) return 'Target key is system-managed'
  }
  return ''
})

const metaValid = computed(() => {
  if (metaError.value) return false
  if (metaMode.value === 'set') return !!metaKey.value && isValidMetaKey(metaKey.value) && isEditableMetaKey(metaKey.value)
  if (metaMode.value === 'remove') return !!metaKey.value
  return !!metaKey.value && !!metaRenameTo.value && isValidMetaKey(metaRenameTo.value) && isEditableMetaKey(metaRenameTo.value)
})

function emitMeta(close: () => void) {
  if (!metaValid.value) return
  if (metaMode.value === 'set') {
    emit('apply', { type: 'setMetadata', key: metaKey.value, value: metaValue.value })
  } else if (metaMode.value === 'remove') {
    emit('apply', { type: 'removeMetadata', key: metaKey.value })
  } else {
    emit('apply', { type: 'renameMetadata', from: metaKey.value, to: metaRenameTo.value })
  }
  metaKey.value = ''
  metaValue.value = ''
  metaRenameTo.value = ''
  close()
}

// Keep TS from flagging props as only template-referenced.
void props
</script>
