<template>
  <div class="flex flex-col h-full">
    <!-- Page header -->
    <div class="mb-4 flex-none">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">AI Assistant</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">
        Create and manage import rules by uploading a sample file and describing what you need
      </p>
    </div>

    <!-- LLM not configured banner -->
    <div
      v-if="!llmConfigured"
      class="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-700 dark:bg-amber-900/20 p-5 mb-4 flex-none"
    >
      <div class="flex items-start gap-3">
        <svg class="h-5 w-5 text-amber-500 mt-0.5 flex-none" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
        </svg>
        <div>
          <h3 class="font-semibold text-amber-800 dark:text-amber-300">LLM not configured</h3>
          <p class="mt-1 text-sm text-amber-700 dark:text-amber-400">
            The AI assistant requires an LLM to be configured. Go to
            <RouterLink to="/settings" class="underline font-medium hover:text-amber-900 dark:hover:text-amber-200">
              Settings → AI &amp; LLM
            </RouterLink>
            and set your provider, API key, and model name.
          </p>
          <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
            Both cloud providers (OpenAI, Anthropic, Groq) and local models via
            LM Studio or Ollama are supported.
          </p>
        </div>
      </div>
    </div>

    <!-- Chat UI -->
    <div
      v-else
      class="flex flex-col flex-1 min-h-0 bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700 overflow-hidden"
    >
      <!-- Message list -->
      <div ref="messageListEl" class="flex-1 overflow-y-auto p-4 space-y-4">
        <!-- Empty state -->
        <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-center py-12">
          <div class="rounded-full bg-indigo-100 dark:bg-indigo-900/30 p-4 mb-4">
            <svg class="h-8 w-8 text-indigo-600 dark:text-indigo-400" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
            </svg>
          </div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-white">Ready to help</h3>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400 max-w-sm">
            Upload a CSV, XLS, or .eml file and ask me to create an import rule for it.
            You can also ask me to adjust an existing rule.
          </p>
        </div>

        <!-- Messages -->
        <template v-for="(msg, idx) in messages" :key="idx">
          <!-- User message -->
          <div v-if="msg.role === 'user'" class="flex justify-end">
            <div class="max-w-[75%]">
              <div
                class="rounded-2xl rounded-tr-sm px-4 py-2.5 bg-indigo-600 text-white text-sm whitespace-pre-wrap"
              >{{ msg.content }}</div>
              <div v-if="msg.fileName" class="mt-1 flex justify-end">
                <span class="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                  <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
                  </svg>
                  {{ msg.fileName }}
                </span>
              </div>
            </div>
          </div>

          <!-- Assistant message -->
          <div v-else class="flex justify-start">
            <div class="max-w-[85%] space-y-2">
              <!-- Tool call badges -->
              <template v-if="msg.toolEvents && msg.toolEvents.length">
                <div
                  v-for="(te, ti) in msg.toolEvents"
                  :key="ti"
                  class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium"
                  :class="te.done
                    ? te.success
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                    : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300 animate-pulse'"
                >
                  <svg v-if="!te.done" class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  <svg v-else-if="te.success" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  <svg v-else class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  {{ te.message }}
                </div>
              </template>

              <!-- Text content (rendered as markdown-ish) -->
              <div
                v-if="msg.content"
                class="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm prose prose-sm dark:prose-invert max-w-none prose-pre:bg-gray-200 dark:prose-pre:bg-gray-800"
                v-html="renderMarkdown(msg.content)"
              />

              <!-- Streaming cursor -->
              <div
                v-if="msg.streaming && !msg.content"
                class="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-400 text-sm"
              >
                <span class="inline-block w-2 h-4 bg-gray-400 animate-pulse rounded-sm" />
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- Input area -->
      <div class="flex-none border-t border-gray-200 dark:border-gray-700 p-4">
        <!-- File attachment preview -->
        <div v-if="attachedFile" class="mb-2 flex items-center gap-2">
          <span class="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-700 px-3 py-1 text-xs text-indigo-700 dark:text-indigo-300">
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
            </svg>
            {{ attachedFile.name }}
          </span>
          <button
            type="button"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            title="Remove file"
            @click="attachedFile = null"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="flex gap-2 items-end">
          <!-- File attach button -->
          <button
            type="button"
            class="flex-none p-2 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="Attach a CSV, XLS, or .eml file"
            :disabled="streaming"
            @click="fileInputEl?.click()"
          >
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
            </svg>
          </button>
          <input
            ref="fileInputEl"
            type="file"
            accept=".csv,.xls,.xlsx,.xlsm,.eml,.yaml,.yml"
            class="hidden"
            @change="handleFileSelected"
          />

          <!-- Text input -->
          <textarea
            ref="textareaEl"
            v-model="inputText"
            class="flex-1 resize-none rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:focus:ring-indigo-400 placeholder-gray-400 dark:placeholder-gray-500 min-h-[40px] max-h-[160px]"
            placeholder="Ask me to create or modify an import rule..."
            rows="1"
            :disabled="streaming"
            @input="autoResize"
            @keydown.enter.exact.prevent="sendMessage"
          />

          <!-- Send button -->
          <button
            type="button"
            class="flex-none p-2 rounded-lg transition-colors"
            :class="canSend
              ? 'bg-indigo-600 text-white hover:bg-indigo-700'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-400 cursor-not-allowed'"
            :disabled="!canSend"
            @click="sendMessage"
          >
            <svg v-if="!streaming" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
            </svg>
            <svg v-else class="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
          </button>
        </div>

        <p class="mt-2 text-xs text-gray-400 dark:text-gray-500">
          Accepts CSV, XLS/XLSX, and .eml files &middot; Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { streamAssistantChat, readFileAsBase64 } from '@/api/assistant'
import type { AttachedFile, ChatMessage } from '@/api/assistant'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ToolEvent {
  tool: string
  message: string
  done: boolean
  success: boolean
}

interface DisplayMessage {
  role: 'user' | 'assistant'
  content: string
  fileName?: string           // for user messages with an attachment
  toolEvents?: ToolEvent[]    // for assistant messages
  streaming?: boolean
}

// ── State ─────────────────────────────────────────────────────────────────────

const llmConfigured = ref(true) // optimistic; corrected on mount
const messages = ref<DisplayMessage[]>([])
const inputText = ref('')
const attachedFile = ref<AttachedFile | null>(null)
const streaming = ref(false)

const textareaEl = ref<HTMLTextAreaElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
const messageListEl = ref<HTMLDivElement | null>(null)

const canSend = computed(() =>
  !streaming.value && (inputText.value.trim().length > 0 || attachedFile.value !== null)
)

// ── LLM config check ──────────────────────────────────────────────────────────

onMounted(async () => {
  try {
    const res = await fetch('/api/config')
    if (res.ok) {
      const data = await res.json()
      const model = data?.data?.ai?.llm?.model ?? ''
      llmConfigured.value = model.trim().length > 0
    }
  } catch {
    // If we can't reach the backend, show the chat anyway and let the
    // server return an error when the user sends a message.
    llmConfigured.value = true
  }
})

// ── File handling ─────────────────────────────────────────────────────────────

async function handleFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    attachedFile.value = await readFileAsBase64(file)
  } catch (err) {
    console.error('Failed to read file', err)
  }
  // Reset input so the same file can be reselected
  input.value = ''
}

// ── Send message ──────────────────────────────────────────────────────────────

async function sendMessage() {
  if (!canSend.value) return

  const text = inputText.value.trim()
  const file = attachedFile.value

  if (!text && !file) return

  // Add user message to display
  messages.value.push({
    role: 'user',
    content: text || `(attached: ${file!.name})`,
    fileName: file?.name,
  })

  inputText.value = ''
  attachedFile.value = null
  resetTextareaHeight()
  scrollToBottom()

  // Build conversation history for the API (text-only, no file content)
  const apiMessages: ChatMessage[] = messages.value
    .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.content))
    .map((m) => ({ role: m.role, content: m.content }))

  // Add a placeholder assistant message
  const assistantMsg: DisplayMessage = {
    role: 'assistant',
    content: '',
    toolEvents: [],
    streaming: true,
  }
  messages.value.push(assistantMsg)
  streaming.value = true

  try {
    for await (const event of streamAssistantChat(apiMessages, file, { page: 'assistant' })) {
      if (event.type === 'token') {
        assistantMsg.content += event.content
        scrollToBottom()
      } else if (event.type === 'tool_start') {
        assistantMsg.toolEvents!.push({
          tool: event.tool,
          message: event.message,
          done: false,
          success: true,
        })
        scrollToBottom()
      } else if (event.type === 'tool_result') {
        const te = assistantMsg.toolEvents!.find((t) => t.tool === event.tool && !t.done)
        if (te) {
          te.done = true
          te.success = event.success
          te.message = event.message
        }
        scrollToBottom()
      } else if (event.type === 'error') {
        assistantMsg.content += `\n\n**Error:** ${event.message}`
        scrollToBottom()
      } else if (event.type === 'done') {
        break
      }
    }
  } catch (err: unknown) {
    assistantMsg.content += `\n\n**Error:** ${err instanceof Error ? err.message : 'Unknown error'}`
  } finally {
    assistantMsg.streaming = false
    streaming.value = false
    scrollToBottom()
    await nextTick()
    textareaEl.value?.focus()
  }
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function scrollToBottom() {
  nextTick(() => {
    if (messageListEl.value) {
      messageListEl.value.scrollTop = messageListEl.value.scrollHeight
    }
  })
}

function autoResize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function resetTextareaHeight() {
  if (textareaEl.value) textareaEl.value.style.height = 'auto'
}

// Very simple markdown renderer: bold, code blocks, inline code, paragraphs
function renderMarkdown(text: string): string {
  return text
    // Fenced code blocks
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="overflow-x-auto rounded p-2 text-xs"><code>$2</code></pre>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="rounded bg-gray-200 dark:bg-gray-700 px-1 py-0.5 text-xs font-mono">$1</code>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Paragraphs (double newline)
    .replace(/\n\n/g, '</p><p class="mt-2">')
    // Single newline
    .replace(/\n/g, '<br/>')
    // Wrap in paragraph
    .replace(/^/, '<p>')
    .replace(/$/, '</p>')
}
</script>
