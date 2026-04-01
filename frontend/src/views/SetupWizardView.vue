<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-start justify-center pt-[15vh] p-4">
    <div class="w-full max-w-lg">
      <!-- Progress indicator -->
      <div class="mb-8 flex items-center justify-center gap-2">
        <div
          v-for="s in 4"
          :key="s"
          class="h-2 w-12 rounded-full transition-colors duration-200"
          :class="s <= step ? 'bg-indigo-600 dark:bg-indigo-500' : 'bg-gray-200 dark:bg-gray-700'"
        />
      </div>

      <!-- Card -->
      <div class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">

        <!-- Step 1: Welcome + Currency -->
        <div v-if="step === 1" class="p-6 sm:p-8">
          <h1 class="text-xl font-semibold text-gray-900 dark:text-white">Welcome to FinzyTrack</h1>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            We'll ask a few quick questions to set up your preferences. These preferences can be changed later.
          </p>

          <div class="mt-6">
            <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">
              Default currency
            </label>
            <p class="text-xs text-gray-400 dark:text-gray-500 mb-2">
              Pick from the list or type a custom currency code.
            </p>
            <Combobox as="div" v-model="currency" @update:modelValue="currency = $event">
              <div class="relative">
                <ComboboxInput
                  class="block w-full rounded-md bg-white py-1.5 pr-12 pl-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                  placeholder="e.g. USD, EUR, INR"
                  @change="currencyQuery = $event.target.value"
                  :display-value="(v: unknown) => typeof v === 'string' ? v : ''"
                  autocomplete="off"
                />
                <ComboboxButton class="absolute inset-y-0 right-0 flex items-center rounded-r-md px-2 focus:outline-hidden">
                  <ChevronUpDownIcon class="size-5 text-gray-400" aria-hidden="true" />
                </ComboboxButton>

                <transition leave-active-class="transition ease-in duration-100" leave-to-class="opacity-0">
                  <ComboboxOptions
                    v-if="filteredCurrencies.length > 0 || currencyQuery.length > 0"
                    class="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-base shadow-lg outline outline-black/5 sm:text-sm dark:bg-gray-800 dark:shadow-none dark:-outline-offset-1 dark:outline-white/10"
                  >
                    <!-- Custom entry -->
                    <ComboboxOption
                      v-if="customCurrencyOption"
                      :value="customCurrencyOption"
                      as="template"
                      v-slot="{ active }"
                    >
                      <li :class="['relative cursor-default px-3 py-2 select-none', active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white']">
                        {{ currencyQuery.toUpperCase() }} <span class="text-sm opacity-75 ml-2">(custom)</span>
                      </li>
                    </ComboboxOption>

                    <ComboboxOption
                      v-for="c in filteredCurrencies"
                      :key="c.code"
                      :value="c.code"
                      as="template"
                      v-slot="{ active }"
                    >
                      <li :class="['relative cursor-default px-3 py-2 select-none', active ? 'bg-indigo-600 text-white dark:bg-indigo-500' : 'text-gray-900 dark:text-white']">
                        <span class="font-medium">{{ c.code }}</span>
                        <span class="ml-2 text-sm opacity-75">{{ c.name }}</span>
                      </li>
                    </ComboboxOption>
                  </ComboboxOptions>
                </transition>
              </div>
            </Combobox>
          </div>

          <div class="mt-8 flex justify-end">
            <button
              @click="step = 2"
              :disabled="!currency"
              class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
            >
              Next
            </button>
          </div>
        </div>

        <!-- Step 2: Ledger -->
        <div v-if="step === 2" class="p-6 sm:p-8">
          <h1 class="text-xl font-semibold text-gray-900 dark:text-white">Ledger file</h1>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            FinzyTrack uses a <a href="https://beancount.github.io/" target="_blank" rel="noopener noreferrer" class="text-indigo-600 dark:text-indigo-400 underline underline-offset-2">Beancount</a> plain-text ledger to store your financial data.
          </p>

          <fieldset class="mt-6 space-y-3">
            <label
              class="flex items-start gap-3 rounded-lg border p-4 cursor-pointer transition-colors"
              :class="ledgerMode === 'fresh' ? 'border-indigo-600 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-900/20' : 'border-gray-200 dark:border-white/10 hover:border-gray-300 dark:hover:border-white/20'"
            >
              <input type="radio" v-model="ledgerMode" value="fresh" class="mt-0.5 text-indigo-600 focus:ring-indigo-600" />
              <div>
                <span class="text-sm font-medium text-gray-900 dark:text-white">Start fresh</span>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  Create a starter ledger with common expense accounts. You can add, rename, or remove these accounts later in the Accounts panel.
                </p>
              </div>
            </label>

            <label
              class="flex items-start gap-3 rounded-lg border p-4 cursor-pointer transition-colors"
              :class="ledgerMode === 'existing' ? 'border-indigo-600 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-900/20' : 'border-gray-200 dark:border-white/10 hover:border-gray-300 dark:hover:border-white/20'"
            >
              <input type="radio" v-model="ledgerMode" value="existing" class="mt-0.5 text-indigo-600 focus:ring-indigo-600" />
              <div>
                <span class="text-sm font-medium text-gray-900 dark:text-white">Use an existing Beancount file</span>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  Import a file you already have. Only self-contained files are supported (no <code class="font-mono bg-gray-100 dark:bg-gray-400/10 px-1 rounded">include</code> directives or references to other files).
                </p>
              </div>
            </label>
          </fieldset>

          <!-- File picker for existing ledger -->
          <div v-if="ledgerMode === 'existing'" class="mt-4">
            <div class="rounded-lg border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-3 mb-3">
              <p class="text-xs text-amber-700 dark:text-amber-400">
                We recommend making a backup copy of your Beancount file before proceeding.
              </p>
            </div>
            <div class="flex gap-2">
              <input
                v-model="existingLedgerPath"
                type="text"
                placeholder="/path/to/your/ledger.beancount"
                class="block flex-1 rounded-md bg-white py-1.5 px-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
              />
              <button
                @click="showFilePicker = true"
                class="rounded-md bg-white px-3 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
              >
                Browse
              </button>
            </div>
          </div>

          <div class="mt-8 flex justify-between">
            <button
              @click="step = 1"
              class="rounded-md bg-white px-4 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
            >
              Back
            </button>
            <button
              @click="step = 3"
              :disabled="ledgerMode === 'existing' && !existingLedgerPath.trim()"
              class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
            >
              Next
            </button>
          </div>
        </div>

        <!-- Step 3: AI Configuration -->
        <div v-if="step === 3" class="p-6 sm:p-8">
          <h1 class="text-xl font-semibold text-gray-900 dark:text-white">AI model <span class="text-sm font-normal text-gray-400 dark:text-gray-500">(optional)</span></h1>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            FinzyTrack works fully without AI. An AI model makes certain tasks faster — creating import rules, parsing bank statements without rules, and conversational finance queries.
          </p>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            A capable model with tool-calling support, 128k+ context, and 32B+ active parameters is recommended. For example, GLM-4.7 has been tested with satisfactory results.
            <a href="https://finzytrack.app/docs/ai-setup" target="_blank" rel="noopener noreferrer" class="text-indigo-600 dark:text-indigo-400 underline underline-offset-2">Learn more</a>.
          </p>

          <div class="mt-6 space-y-4">
            <div class="flex items-center gap-3">
              <button
                @click="configureAI = !configureAI"
                :class="configureAI ? 'bg-indigo-600 dark:bg-indigo-500' : 'bg-gray-200 dark:bg-gray-700'"
                class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                role="switch"
                :aria-checked="configureAI"
              >
                <span
                  :class="configureAI ? 'translate-x-5' : 'translate-x-0'"
                  class="pointer-events-none relative inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                />
              </button>
              <span class="text-sm font-medium text-gray-900 dark:text-white">Configure an AI model now</span>
            </div>

            <template v-if="configureAI">
              <div>
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Provider</label>
                <select
                  v-model="aiProvider"
                  class="mt-1 block w-full rounded-md bg-white py-1.5 pr-10 pl-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
                >
                  <option value="openai">OpenAI-compatible (LM Studio, Ollama, OpenAI, Groq, etc.)</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>

              <div v-if="aiProvider === 'openai'">
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">API URL</label>
                <input
                  v-model="aiApiUrl"
                  type="text"
                  placeholder="e.g. http://127.0.0.1:1234 or https://api.openai.com"
                  class="mt-1 block w-full rounded-md bg-white py-1.5 px-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                />
              </div>

              <div>
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">API key</label>
                <input
                  v-model="aiApiKey"
                  type="password"
                  placeholder="Required for cloud providers, leave empty for local LLMs"
                  class="mt-1 block w-full rounded-md bg-white py-1.5 px-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                />
              </div>

              <div>
                <label class="block text-sm/6 font-medium text-gray-900 dark:text-white">Model</label>
                <input
                  v-model="aiModel"
                  type="text"
                  placeholder="e.g. glm-4-32b-0414, gpt-4o, claude-sonnet-4-6"
                  class="mt-1 block w-full rounded-md bg-white py-1.5 px-3 text-base text-gray-900 outline outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 sm:text-sm dark:bg-white/5 dark:text-white dark:outline-white/10 dark:placeholder:text-gray-500 dark:focus:outline-indigo-500"
                />
              </div>
            </template>
          </div>

          <div class="mt-8 flex justify-between">
            <button
              @click="step = 2"
              class="rounded-md bg-white px-4 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
            >
              Back
            </button>
            <button
              @click="completeSetup"
              :disabled="isSubmitting || (configureAI && !aiModel.trim())"
              class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 flex items-center gap-2"
            >
              <div v-if="isSubmitting" class="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
              {{ isSubmitting ? 'Setting up...' : 'Finish setup' }}
            </button>
          </div>

          <!-- Error -->
          <div v-if="setupError" class="mt-4 rounded-lg border border-red-200 dark:border-red-700 bg-red-50 dark:bg-red-900/20 p-3">
            <p class="text-sm text-red-700 dark:text-red-400">{{ setupError }}</p>
          </div>
        </div>

        <!-- Step 4: Done -->
        <div v-if="step === 4" class="p-6 sm:p-8 text-center">
          <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
            <CheckIcon class="h-6 w-6 text-green-600 dark:text-green-400" />
          </div>
          <h1 class="mt-4 text-xl font-semibold text-gray-900 dark:text-white">You're all set!</h1>
          <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">
            Your ledger is ready with <strong>{{ currency }}</strong> as the default currency.
          </p>
          <p v-if="configureAI && aiModel" class="mt-1 text-sm text-gray-600 dark:text-gray-400">
            AI model <strong>{{ aiModel }}</strong> has been configured.
          </p>

          <div class="mt-6 space-y-2 text-left bg-gray-50 dark:bg-gray-800 rounded-lg p-4 text-sm text-gray-600 dark:text-gray-400">
            <p class="font-medium text-gray-900 dark:text-white">Next steps</p>
            <ul class="list-disc list-inside space-y-1">
              <li>Go to <strong>Accounts</strong> and review/adjust accounts.</li>
              <li>Go to <strong>Import</strong> and import account statements.</li>
            </ul>
          </div>

          <div class="mt-8 flex justify-center">
            <button
              @click="goTo('/accounts')"
              class="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400"
            >
              Go to Accounts
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- File picker modal -->
    <FilePickerModal
      v-if="showFilePicker"
      :open="showFilePicker"
      title="Select Beancount ledger file"
      mode="file"
      :extensions="['.beancount', '.bean', '.bc']"
      @close="showFilePicker = false"
      @select="handleFileSelect"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { CheckIcon } from '@heroicons/vue/24/outline'
import { ChevronUpDownIcon } from '@heroicons/vue/20/solid'
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/vue'
import FilePickerModal from '@/components/common/FilePickerModal.vue'
import { SetupService, ApiError } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'
// @ts-ignore — useTheme is a .js file without type declarations
import { useTheme } from '@/composables/useTheme'

// Initialize theme (normally done by AppShell, which is bypassed during setup)
useTheme()

const router = useRouter()
const { reloadConfig } = useConfig()

// Wizard state
const step = ref(1)
const isSubmitting = ref(false)
const setupError = ref<string | null>(null)

// Step 1: Currency
const currency = ref('')
const currencyQuery = ref('')

const CURRENCIES = [
  { code: 'USD', name: 'US Dollar' },
  { code: 'EUR', name: 'Euro' },
  { code: 'GBP', name: 'British Pound' },
  { code: 'INR', name: 'Indian Rupee' },
  { code: 'CAD', name: 'Canadian Dollar' },
  { code: 'AUD', name: 'Australian Dollar' },
  { code: 'JPY', name: 'Japanese Yen' },
  { code: 'CNY', name: 'Chinese Yuan' },
  { code: 'CHF', name: 'Swiss Franc' },
  { code: 'SGD', name: 'Singapore Dollar' },
  { code: 'HKD', name: 'Hong Kong Dollar' },
  { code: 'NZD', name: 'New Zealand Dollar' },
  { code: 'SEK', name: 'Swedish Krona' },
  { code: 'NOK', name: 'Norwegian Krone' },
  { code: 'DKK', name: 'Danish Krone' },
  { code: 'KRW', name: 'South Korean Won' },
  { code: 'BRL', name: 'Brazilian Real' },
  { code: 'ZAR', name: 'South African Rand' },
  { code: 'MXN', name: 'Mexican Peso' },
  { code: 'AED', name: 'UAE Dirham' },
  { code: 'THB', name: 'Thai Baht' },
  { code: 'MYR', name: 'Malaysian Ringgit' },
  { code: 'IDR', name: 'Indonesian Rupiah' },
  { code: 'PHP', name: 'Philippine Peso' },
  { code: 'PLN', name: 'Polish Zloty' },
  { code: 'TRY', name: 'Turkish Lira' },
  { code: 'TWD', name: 'Taiwan Dollar' },
  { code: 'SAR', name: 'Saudi Riyal' },
  { code: 'ILS', name: 'Israeli Shekel' },
  { code: 'CZK', name: 'Czech Koruna' },
]

const filteredCurrencies = computed(() => {
  if (!currencyQuery.value) return CURRENCIES
  const q = currencyQuery.value.toLowerCase()
  return CURRENCIES.filter(
    c => c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)
  )
})

const customCurrencyOption = computed(() => {
  if (!currencyQuery.value) return null
  const upper = currencyQuery.value.toUpperCase()
  if (CURRENCIES.some(c => c.code === upper)) return null
  return upper
})

// Step 2: Ledger
const ledgerMode = ref<'fresh' | 'existing'>('fresh')
const existingLedgerPath = ref('')
const showFilePicker = ref(false)

const handleFileSelect = (path: string) => {
  existingLedgerPath.value = path
  showFilePicker.value = false
}

// Step 3: AI
const configureAI = ref(false)
const aiProvider = ref('openai')
const aiApiUrl = ref('')
const aiApiKey = ref('')
const aiModel = ref('')

// Actions
const completeSetup = async () => {
  isSubmitting.value = true
  setupError.value = null

  try {
    const request: any = {
      currency: currency.value,
      ledger_mode: ledgerMode.value,
    }

    if (ledgerMode.value === 'existing') {
      request.existing_ledger_path = existingLedgerPath.value.trim()
    }

    if (configureAI.value && aiModel.value.trim()) {
      request.ai_config = {
        provider: aiProvider.value,
        model: aiModel.value.trim(),
      }
      if (aiProvider.value === 'openai' && aiApiUrl.value.trim()) {
        request.ai_config.api_url = aiApiUrl.value.trim()
      }
      if (aiApiKey.value.trim()) {
        request.ai_config.api_key = aiApiKey.value.trim()
      }
    }

    await SetupService.completeSetupApiSetupCompletePost(request)
    await reloadConfig()
    step.value = 4
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      setupError.value = error.body?.error?.message || error.message
    } else if (error instanceof Error) {
      setupError.value = error.message
    } else {
      setupError.value = 'Setup failed. Please try again.'
    }
  } finally {
    isSubmitting.value = false
  }
}

const goTo = (path: string) => {
  router.push(path)
}
</script>
