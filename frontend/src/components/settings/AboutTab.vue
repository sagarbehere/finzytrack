<template>
  <div class="space-y-6">
    <!-- About -->
    <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">About</h3>
      </div>
      <div class="p-6 space-y-3">
        <div class="flex items-baseline gap-3">
          <span class="text-2xl font-semibold text-gray-900 dark:text-white">Finzytrack</span>
          <span class="text-lg text-gray-500 dark:text-gray-400">v{{ appVersion }}</span>
        </div>
        <p class="text-sm text-gray-600 dark:text-gray-400">
          Personal finance that stays personal.
        </p>
        <p class="text-sm text-gray-600 dark:text-gray-400">
          Released under
          <a
            href="https://www.gnu.org/licenses/old-licenses/gpl-2.0.html"
            target="_blank"
            rel="noopener noreferrer"
            class="text-indigo-600 hover:underline dark:text-indigo-400"
          >GPL v2</a>
          — open source, forever.
        </p>
      </div>
    </div>

    <!-- Resources -->
    <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">Resources</h3>
      </div>
      <div class="p-6">
        <ul class="space-y-2 text-sm">
          <li v-for="link in resourceLinks" :key="link.href" class="flex items-center gap-2">
            <span class="text-gray-500 dark:text-gray-400 w-32">{{ link.label }}</span>
            <a
              :href="link.href"
              target="_blank"
              rel="noopener noreferrer"
              class="text-indigo-600 hover:underline dark:text-indigo-400 break-all"
            >{{ link.href }}</a>
          </li>
        </ul>
      </div>
    </div>

    <!-- Diagnostics -->
    <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">Diagnostics</h3>
        <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
          Useful when reporting a bug. Copy this to your clipboard, or use
          <span class="font-medium">Report an issue</span> below to open a pre-filled GitHub issue.
        </p>
      </div>
      <div class="p-6 space-y-4">
        <pre class="rounded-md bg-gray-50 p-4 text-sm font-mono text-gray-800 overflow-x-auto dark:bg-white/5 dark:text-gray-200">{{ diagnosticsText }}</pre>
        <div class="flex flex-wrap items-center gap-3">
          <button
            type="button"
            @click="copyDiagnostics"
            :disabled="loading"
            class="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {{ copied ? 'Copied!' : 'Copy diagnostics' }}
          </button>
          <a
            :href="reportIssueUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 dark:bg-indigo-500 dark:shadow-none dark:hover:bg-indigo-400 dark:focus-visible:outline-indigo-500"
          >
            Report an issue
          </a>
          <p v-if="loadError" class="text-sm text-red-600 dark:text-red-400">{{ loadError }}</p>
        </div>
      </div>
    </div>

    <!-- Acknowledgements -->
    <div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10">
      <div class="px-6 py-4 border-b border-gray-200 dark:border-white/10">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">Acknowledgements</h3>
      </div>
      <div class="p-6">
        <p class="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
          Finzytrack stands on a deep stack of open-source work. Built with<template
            v-for="(ack, i) in acknowledgements" :key="ack.href"
          > <a
              :href="ack.href" target="_blank" rel="noopener noreferrer"
              class="text-indigo-600 hover:underline dark:text-indigo-400"
            >{{ ack.name }}</a>{{ separator(i) }}</template>.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { DefaultService } from '@/services/generated-api'
import { errorHandler } from '@/utils/ErrorHandler'

const appVersion = ref('—')
const pythonVersion = ref('—')
const platformStr = ref('—')
const loading = ref(true)
const loadError = ref<string | null>(null)
const copied = ref(false)

const resourceLinks = [
  { label: 'Documentation', href: 'https://docs.finzytrack.com' },
  { label: 'Website', href: 'https://finzytrack.com' },
  { label: 'Source code', href: 'https://github.com/sagarbehere/finzytrack' },
  { label: 'Report an issue', href: 'https://github.com/sagarbehere/finzytrack/issues' },
]

const acknowledgements = [
  { name: 'Beancount', href: 'https://beancount.github.io/' },
  { name: 'SQLite', href: 'https://sqlite.org/' },
  { name: 'FastAPI', href: 'https://fastapi.tiangolo.com/' },
  { name: 'Vue 3', href: 'https://vuejs.org/' },
  { name: 'Vite', href: 'https://vitejs.dev/' },
  { name: 'ECharts', href: 'https://echarts.apache.org/' },
  { name: 'Tailwind CSS', href: 'https://tailwindcss.com/' },
  { name: 'PyInstaller', href: 'https://pyinstaller.org/' },
  { name: 'PyWebView', href: 'https://pywebview.flowrl.com/' },
]

// Comma-separated list with an Oxford "and" before the last item.
function separator(i: number): string {
  if (i === acknowledgements.length - 1) return ''
  if (i === acknowledgements.length - 2) return ', and '
  return ', '
}

const diagnosticsText = computed(() =>
  `Finzytrack ${appVersion.value}\n` +
  `OS: ${platformStr.value}\n` +
  `Python: ${pythonVersion.value}`
)

const reportIssueUrl = computed(() => {
  const body =
    `**Describe the issue:**\n\n\n\n` +
    `**Steps to reproduce:**\n1. \n2. \n\n` +
    `**Diagnostics:**\n\`\`\`\n${diagnosticsText.value}\n\`\`\`\n`
  return `https://github.com/sagarbehere/finzytrack/issues/new?body=${encodeURIComponent(body)}`
})

async function copyDiagnostics() {
  try {
    await navigator.clipboard.writeText(diagnosticsText.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch (err) {
    // Fall back to displaying — let the user select & copy manually
    errorHandler.display(err)
  }
}

onMounted(async () => {
  try {
    const res = await DefaultService.healthHealthGet() as {
      details?: { app_version?: string; python_version?: string; platform?: string }
    }
    const d = res?.details ?? {}
    if (d.app_version) appVersion.value = d.app_version
    if (d.python_version) pythonVersion.value = d.python_version
    if (d.platform) platformStr.value = d.platform
  } catch (err) {
    loadError.value = 'Could not load diagnostics. The values above may be incomplete.'
    errorHandler.display(err)
  } finally {
    loading.value = false
  }
})
</script>
