<template>
  <div class="flex flex-col h-full">
    <!-- Page header -->
    <div class="mb-4 flex-none flex items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">AI Assistant</h1>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
          AI can make mistakes — review output carefully.
          <a href="https://docs.finzytrack.com/reference/ai-data-sharing/" target="_blank" rel="noopener noreferrer" class="text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2">Data shared with AI</a>
        </p>
      </div>
      <button
        type="button"
        class="flex-none inline-flex items-center gap-1.5 rounded-md bg-white px-2.5 py-1.5 text-sm font-semibold text-gray-900 shadow-xs inset-ring inset-ring-gray-300 hover:bg-gray-50 dark:bg-white/10 dark:text-white dark:shadow-none dark:inset-ring-white/5 dark:hover:bg-white/20"
        title="Discard this conversation and start a new one"
        @click="resetChat"
      >
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
        </svg>
        New chat
      </button>
    </div>

    <!-- AI not configured banner -->
    <div
      v-if="!llmConfigured"
      class="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-700 dark:bg-amber-900/20 p-5 mb-4 flex-none"
    >
      <div class="flex items-start gap-3">
        <svg class="h-5 w-5 text-amber-500 mt-0.5 flex-none" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
        </svg>
        <div>
          <h3 class="font-semibold text-amber-800 dark:text-amber-300">AI not configured</h3>
          <p class="mt-1 text-sm text-amber-700 dark:text-amber-400">
            The AI assistant requires an AI model to be configured. Go to
            <RouterLink to="/settings" class="underline font-medium hover:text-amber-900 dark:hover:text-amber-200">
              Settings → AI
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
      class="flex flex-col flex-1 min-h-0 overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200 dark:bg-gray-800/50 dark:shadow-none dark:ring-white/10 overflow-hidden"
    >
      <!-- Content row: chat pane + sidebar -->
      <div class="flex flex-1 min-h-0 overflow-hidden">

        <!-- Chat pane -->
        <div
          class="flex flex-col min-h-0 overflow-hidden"
          :style="{ width: sidebarOpen ? `${100 - sidebarWidthPct}%` : '100%' }"
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
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400 max-w-md">
            Ask about your finances, build a dashboard, or attach a file to create import rules.
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
                <button
                  v-if="msg.fileSheets"
                  class="inline-flex items-center gap-1 text-xs text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2 cursor-pointer"
                  title="Click to reopen file preview"
                  @click="previewSheets = msg.fileSheets!; previewFileName = msg.fileName!; sidebarMode = 'filePreview'"
                >
                  <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
                  </svg>
                  {{ msg.fileName }}
                </button>
                <span v-else class="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
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
              <!-- Reasoning: auto-expanded while streaming, auto-collapsed on completion. -->
              <details v-if="msg.thinking" :open="msg.streaming" class="group rounded-lg bg-gray-50 dark:bg-white/[0.03] ring-1 ring-gray-200 dark:ring-white/10">
                <summary class="flex cursor-pointer items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 select-none hover:text-gray-700 dark:hover:text-gray-300">
                  <svg class="h-3 w-3 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                  </svg>
                  <span>
                    <span class="font-semibold">{{ msg.streaming ? 'Model is reasoning' : 'Reasoning' }}</span>
                    <span class="ml-1 font-normal italic text-gray-400 dark:text-gray-500">
                      <template v-if="msg.streaming">(internal — the answer will follow)</template>
                      <template v-else>· {{ msg.thinking.length.toLocaleString() }} chars</template>
                    </span>
                    <span v-if="msg.streaming" class="ml-1 font-normal text-gray-400 dark:text-gray-500">
                      · {{ msg.thinking.length.toLocaleString() }} chars
                    </span>
                  </span>
                  <a
                    :href="REASONING_DOCS_URL"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="ml-auto inline-flex h-4 w-4 items-center justify-center rounded-full ring-1 ring-gray-300 dark:ring-white/20 text-[10px] text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:ring-indigo-300 dark:hover:ring-indigo-400/40"
                    title="What is this? Learn about reasoning models"
                    @click.stop
                  >?</a>
                </summary>
                <div class="px-3 pb-2 text-xs text-gray-500 dark:text-gray-400 whitespace-pre-wrap max-h-64 overflow-y-auto">
                  <template v-if="msg.streaming">
                    <template v-if="msg.thinkingSnippet">
                      <div class="not-italic text-gray-400 dark:text-gray-500 mb-1">First {{ THINKING_SNIPPET_CHARS }} chars of reasoning:</div>
                      <div>{{ msg.thinkingSnippet }}</div>
                      <div class="not-italic text-gray-400 dark:text-gray-500 mt-2">… (full reasoning will appear here when the model finishes)</div>
                    </template>
                    <template v-else>
                      <span class="text-gray-400 dark:text-gray-500">Reasoning is just starting…</span>
                    </template>
                  </template>
                  <template v-else>{{ msg.thinking }}</template>
                </div>
              </details>

              <!-- Tool calls: badge + collapsible details -->
              <template v-if="msg.toolEvents && msg.toolEvents.length">
                <div v-for="(te, ti) in msg.toolEvents" :key="ti" class="space-y-1">
                  <!-- Status badge -->
                  <div
                    class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium"
                    :class="te.done
                      ? te.success
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                      : 'bg-gray-100 text-gray-600 dark:bg-white/5 dark:text-gray-300 animate-pulse'"
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
                  <!-- Collapsible tool details -->
                  <details v-if="te.done && (te.args || te.data)" class="group rounded-lg bg-gray-50 dark:bg-white/[0.03] ring-1 ring-gray-200 dark:ring-white/10">
                    <summary class="flex cursor-pointer items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 select-none hover:text-gray-700 dark:hover:text-gray-300">
                      <svg class="h-3 w-3 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                      </svg>
                      {{ te.tool }} details
                    </summary>
                    <div class="px-3 pb-2 space-y-2 max-h-72 overflow-y-auto">
                      <div v-if="te.args && Object.keys(te.args).length" class="text-xs">
                        <span class="font-medium text-gray-600 dark:text-gray-300">Arguments</span>
                        <pre class="mt-1 rounded bg-gray-200 dark:bg-gray-800 px-2 py-1.5 text-[11px] text-gray-700 dark:text-gray-300 overflow-x-auto whitespace-pre-wrap">{{ formatToolData(te.args) }}</pre>
                      </div>
                      <div v-if="te.data && Object.keys(te.data).length" class="text-xs">
                        <span class="font-medium text-gray-600 dark:text-gray-300">Result</span>
                        <pre class="mt-1 rounded bg-gray-200 dark:bg-gray-800 px-2 py-1.5 text-[11px] text-gray-700 dark:text-gray-300 overflow-x-auto whitespace-pre-wrap">{{ formatToolData(te.data) }}</pre>
                      </div>
                    </div>
                  </details>
                </div>
              </template>

              <!-- Text content (rendered as markdown-ish) -->
              <div
                v-if="msg.content"
                class="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-gray-100 dark:bg-white/5 text-gray-900 dark:text-gray-100 text-sm prose prose-sm dark:prose-invert max-w-none prose-pre:bg-gray-200 dark:prose-pre:bg-gray-800"
                v-html="renderMarkdown(msg.content)"
              />

              <!-- Analyst mode: validation warnings -->
              <template v-if="msg.validationWarnings && msg.validationWarnings.length">
                <div
                  v-for="(warning, wi) in msg.validationWarnings"
                  :key="wi"
                  class="rounded-lg text-xs bg-amber-50 text-amber-800 dark:bg-amber-900/20 dark:text-amber-300 border border-amber-200 dark:border-amber-700/50"
                >
                  <!-- Warning header row -->
                  <div class="flex items-start gap-2 px-3 py-2">
                    <svg class="h-3.5 w-3.5 mt-0.5 flex-none text-amber-500 dark:text-amber-400" viewBox="0 0 20 20" fill="currentColor">
                      <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
                    </svg>
                    <span class="flex-1">{{ warning.message }}</span>
                    <div class="flex items-center gap-1.5 flex-none">
                      <button
                        v-if="warningHasExpandableDetails(warning)"
                        class="text-amber-500 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-200 underline underline-offset-2"
                        @click="warning.expanded = !warning.expanded"
                      >{{ warning.expanded ? 'less' : 'more' }}</button>
                      <button
                        class="text-amber-400 hover:text-amber-600 dark:text-amber-500 dark:hover:text-amber-300"
                        title="Dismiss"
                        @click="msg.validationWarnings!.splice(wi, 1)"
                      >
                        <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                  </div>

                  <!-- Expandable details panel -->
                  <div
                    v-if="warning.expanded && warning.details"
                    class="border-t border-amber-200 dark:border-amber-700/50 px-3 py-2 space-y-1"
                  >
                    <!-- unknown_account: full account list -->
                    <template v-if="warning.rule === 'unknown_account'">
                      <p class="font-medium text-amber-700 dark:text-amber-400 mb-1">All unrecognised accounts:</p>
                      <p
                        v-for="acct in (warning.details.unknown_accounts as string[])"
                        :key="acct"
                        class="font-mono text-amber-800 dark:text-amber-300"
                      >{{ acct }}</p>
                    </template>

                    <!-- date_out_of_range: full date reference list -->
                    <template v-else-if="warning.rule === 'date_out_of_range'">
                      <p class="font-medium text-amber-700 dark:text-amber-400 mb-1">All references outside ledger range:</p>
                      <p
                        v-for="d in (warning.details.out_of_range as string[])"
                        :key="d"
                        class="font-mono text-amber-800 dark:text-amber-300"
                      >{{ d }}</p>
                    </template>

                    <!-- amount_mismatch: max query value for context -->
                    <template v-else-if="warning.rule === 'amount_mismatch'">
                      <p class="text-amber-700 dark:text-amber-400">
                        Largest value in query results:
                        <span class="font-mono font-medium">
                          {{ (warning.details.max_query_value as number).toLocaleString() }}
                        </span>
                      </p>
                    </template>
                  </div>
                </div>
              </template>

              <!-- Validation result (shown after rule is saved) -->
              <div v-if="msg.validationNote" class="space-y-2">

                <!-- Success: simple inline status -->
                <div
                  v-if="msg.validationNote.startsWith('✓')"
                  class="rounded-lg px-3 py-2 text-xs font-medium bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400"
                >{{ msg.validationNote }}</div>

                <!-- Failure: prominent alert with heading, icon, and action button -->
                <div
                  v-else
                  class="rounded-lg p-3 ring-1"
                  :class="isCriticalValidationFailure(msg.validationNote)
                    ? 'bg-red-50 ring-red-300 text-red-700 dark:bg-red-900/20 dark:ring-red-500/30 dark:text-red-300'
                    : 'bg-amber-50 ring-amber-300 text-amber-800 dark:bg-amber-900/20 dark:ring-amber-500/30 dark:text-amber-300'"
                >
                  <div class="flex items-start gap-2">
                    <svg class="h-5 w-5 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.008v.008H12v-.008Z" />
                    </svg>
                    <div class="flex-1 space-y-2">
                      <div class="text-xs font-bold">
                        {{ isCriticalValidationFailure(msg.validationNote) ? 'Rule may be broken' : 'Rule may be incorrect' }}
                      </div>
                      <div class="text-xs">{{ msg.validationNote }}</div>
                      <button
                        type="button"
                        :disabled="streaming"
                        @click="askAssistantToFix(msg)"
                        class="rounded px-2.5 py-1 text-xs font-medium bg-white/60 dark:bg-white/5 ring-1 ring-current/30 hover:bg-white dark:hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed"
                      >Ask assistant to fix</button>
                      <span
                        v-if="msg.validationAutoTriggered"
                        class="ml-2 text-[10px] uppercase tracking-wide opacity-70"
                      >auto-retrying…</span>
                    </div>
                  </div>
                </div>

                <!-- Parsed transactions table -->
                <div
                  v-if="msg.validationTransactions?.length"
                  class="rounded-lg border border-gray-200 dark:border-white/10 overflow-hidden"
                >
                  <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-800/50 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                    Parsed transactions
                  </div>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="border-t border-gray-200 dark:border-white/10 text-gray-400 dark:text-gray-500">
                        <th class="text-left px-3 py-1.5 font-medium">Date</th>
                        <th v-if="hasDescription(msg.validationTransactions)" class="text-left px-3 py-1.5 font-medium">Description</th>
                        <th class="text-right px-3 py-1.5 font-medium">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(tx, i) in validationRows(msg.validationTransactions)"
                        :key="i"
                        class="border-t border-gray-100 dark:border-white/10/50"
                      >
                        <td class="px-3 py-1.5 font-mono text-gray-700 dark:text-gray-300">{{ tx.date }}</td>
                        <td
                          v-if="hasDescription(msg.validationTransactions)"
                          class="px-3 py-1.5 text-gray-600 dark:text-gray-400 max-w-[16rem] truncate"
                        >{{ txDescription(tx) }}</td>
                        <td
                          class="px-3 py-1.5 text-right font-mono"
                          :class="sign(tx.amount) >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'"
                        >{{ formatAmount(tx.amount) }}</td>
                      </tr>
                      <tr v-if="msg.validationTransactions.length > MAX_VALIDATION_ROWS" class="border-t border-gray-100 dark:border-white/10/50">
                        <td colspan="3" class="px-3 py-1.5 text-gray-400 dark:text-gray-500 italic">
                          … {{ msg.validationTransactions.length - MAX_VALIDATION_ROWS }} more rows not shown
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <!-- Email extracted fields table -->
                <div
                  v-if="msg.validationEmailFields?.length"
                  class="rounded-lg border border-gray-200 dark:border-white/10 overflow-hidden"
                >
                  <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-800/50 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                    Extracted fields
                  </div>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="border-t border-gray-200 dark:border-white/10 text-gray-400 dark:text-gray-500">
                        <th class="text-left px-3 py-1.5 font-medium">Field</th>
                        <th class="text-left px-3 py-1.5 font-medium">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(ef, i) in msg.validationEmailFields"
                        :key="i"
                        class="border-t border-gray-100 dark:border-white/10/50"
                      >
                        <td class="px-3 py-1.5 text-gray-600 dark:text-gray-400">
                          {{ ef.label }}<span v-if="ef.optional" class="ml-1 text-gray-400 dark:text-gray-500 italic">(optional)</span>
                        </td>
                        <td
                          class="px-3 py-1.5 font-mono"
                          :class="ef.matched
                            ? 'text-gray-700 dark:text-gray-300'
                            : 'text-red-500 dark:text-red-400'"
                        >{{ ef.matched ? ef.value : (ef.error ?? 'no match') }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <!-- Raw file content -->
                <div
                  v-if="msg.validationRawContent"
                  class="rounded-lg border border-gray-200 dark:border-white/10 overflow-hidden"
                >
                  <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-800/50 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                    Raw file
                  </div>
                  <div class="overflow-auto max-h-48 bg-white dark:bg-gray-800/40">
                    <table class="min-w-full">
                      <tbody>
                        <tr v-for="(line, i) in rawContentLines(msg.validationRawContent)" :key="i">
                          <td class="select-none text-right pr-2 pl-2 py-px font-mono text-xs text-gray-300 dark:text-gray-600 w-8 align-top shrink-0">{{ i + 1 }}</td>
                          <td class="pr-3 py-px font-mono text-xs text-gray-600 dark:text-gray-300 whitespace-pre">{{ line }}</td>
                        </tr>
                        <tr v-if="rawContentOverflow(msg.validationRawContent) > 0">
                          <td></td>
                          <td class="pr-3 py-1 text-xs text-gray-400 dark:text-gray-500 italic">… {{ rawContentOverflow(msg.validationRawContent) }} more lines not shown</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

              </div>

              <!-- Streaming cursor -->
              <div
                v-if="msg.streaming && !msg.content"
                class="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-gray-100 dark:bg-white/5 text-gray-400 text-sm"
              >
                <span class="inline-block w-2 h-4 bg-gray-400 animate-pulse rounded-sm" />
              </div>
            </div>
          </div>
        </template>
      </div>

        </div><!-- end chat pane -->

        <!-- Resize drag handle -->
        <div
          v-if="sidebarOpen"
          class="flex-none w-1.5 cursor-col-resize bg-gray-200 dark:bg-gray-600 hover:bg-indigo-400 dark:hover:bg-indigo-500 active:bg-indigo-500 dark:active:bg-indigo-400 transition-colors"
          @mousedown="startResize"
        />

        <!-- Sidebar -->
        <div
          v-if="sidebarOpen"
          class="flex flex-col min-h-0 overflow-hidden"
          :style="{ width: `${sidebarWidthPct}%` }"
        >
          <!-- Sidebar header -->
          <div class="flex-none flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-gray-800/50">
            <span class="text-xs font-medium text-gray-600 dark:text-gray-400 truncate">
              {{ sidebarMode === 'filePreview' ? previewFileName : previewRecipe?.title ?? 'Dashboard Preview' }}
            </span>
            <button
              class="ml-2 flex-none text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
              title="Close sidebar"
              @click="closeSidebar"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- File preview content -->
          <FilePreviewTable v-if="sidebarMode === 'filePreview' && previewSheets" :sheets="previewSheets" :fill="true" />

          <!-- Recipe preview content -->
          <div v-else-if="sidebarMode === 'recipePreview' && previewRecipe" class="flex-1 overflow-y-auto p-4">
            <RecipeWidget
              v-if="previewRecipeType === 'widget'"
              :recipe="(previewRecipe as JsonWidgetRecipe)"
              class="h-full"
            />
            <RecipeDashboard
              v-else
              :dashboard="(previewRecipe as JsonDashboardRecipe)"
            />
          </div>
        </div>

      </div><!-- end content row -->

      <!-- Input area (full width) -->
      <div class="flex-none border-t border-gray-200 dark:border-white/10 p-4">
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
            @click="attachedFile = null; ruleFilename = ''; ruleAccount = ''; ruleCurrency = ''; expectedTxCount = ''"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Rule quick-fill fields (CSV / XLS only) -->
        <div v-if="showRuleFields" class="mb-2 flex gap-2">
          <div class="flex-1 min-w-0">
            <label class="block text-xs text-gray-400 dark:text-gray-500 mb-0.5">Save as</label>
            <input
              v-model="ruleFilename"
              type="text"
              placeholder="rule-name.yaml"
              class="w-full rounded-md bg-white px-2 py-1.5 text-xs text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
            />
          </div>
          <div class="flex-1 min-w-0">
            <label class="block text-xs text-gray-400 dark:text-gray-500 mb-0.5">Account</label>
            <AccountDropdown
              v-model="ruleAccount"
              placeholder="Assets:Bank:Name"
              :account-types="['Assets', 'Liabilities']"
              :allow-custom="true"
              custom-class="!py-1 !text-xs"
            />
          </div>
          <div class="w-32">
            <label class="block text-xs text-gray-400 dark:text-gray-500 mb-0.5">Currency</label>
            <CommodityDropdown
              v-model="ruleCurrency"
              placeholder="INR"
              :allow-custom="true"
              custom-class="!py-1 !text-xs"
            />
          </div>
          <div v-if="previewSheets" class="w-24">
            <label class="block text-xs text-gray-400 dark:text-gray-500 mb-0.5">Expected txns</label>
            <input
              v-model="expectedTxCount"
              type="number"
              min="1"
              placeholder="# in statement"
              class="w-full rounded-md bg-white px-2 py-1.5 text-xs text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
            />
          </div>
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
            class="flex-1 resize-none rounded-md bg-white px-3 py-1.5 text-sm text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500 min-h-[40px] max-h-[160px]"
            placeholder="Ask a question or describe a dashboard..."
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
              ? 'bg-indigo-600 text-white hover:bg-indigo-500'
              : 'bg-gray-100 dark:bg-white/5 text-gray-400 cursor-not-allowed'"
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
          Attach CSV, XLS, or .eml for import rules &middot; Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

defineOptions({ name: 'AssistantView' })
import { RouterLink } from 'vue-router'
import { streamAssistantChat, readFileAsBase64 } from '@/api/assistant'
import type { AttachedFile, ChatMessage } from '@/api/assistant'
import { ImportService, RecipesService } from '@/services/generated-api'
import type { TrialExtractedField } from '@/services/generated-api'
import { useConfig } from '@/composables/useConfig'
import { parseCsvContent, extractCsvRows } from '@/composables/useCsvParser'
import { parseXlsContent, extractXlsText, extractXlsSheets } from '@/composables/useXlsParser'
import type { CsvParsedTransaction } from '@/types/csv'
import { sign, toNumber, type Money } from '@/utils/money'
import AccountDropdown from '@/components/common/AccountDropdown.vue'
import CommodityDropdown from '@/components/common/CommodityDropdown.vue'
import FilePreviewTable from '@/components/FilePreviewTable.vue'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import RecipeWidget from '@/components/recipes/RecipeWidget.vue'
import type { JsonDashboardRecipe, JsonWidgetRecipe } from '@/types/recipes'
import { resolveRecipeGenerators } from '@/recipes/functions'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ToolEvent {
  tool: string
  message: string
  done: boolean
  success: boolean
  args?: Record<string, unknown>   // tool call arguments
  data?: Record<string, unknown>   // tool result data
}

interface FileSheet {
  name: string
  rows: string[][]
}

interface ValidationWarning {
  rule: string
  severity: 'warn' | 'info'
  message: string
  details?: Record<string, unknown>
  expanded?: boolean
}

// Public docs page explaining reasoning-model behavior. Must match
// _REASONING_DOCS_URL in backend/app/api/routers/assistant.py.
const REASONING_DOCS_URL = 'https://docs.finzytrack.com/reference/reasoning-models'

// While a reasoning block is streaming we render only the live char counter
// and a one-shot snippet captured from the first ~500 chars. This keeps the
// DOM tiny so the JS thread stays responsive even when reasoning runs to
// hundreds of thousands of chars. The full text is rendered once at the end.
const THINKING_SNIPPET_CHARS = 500

interface DisplayMessage {
  role: 'user' | 'assistant'
  content: string
  thinking?: string           // full model reasoning — rendered after streaming ends
  thinkingSnippet?: string    // first ~500 chars, captured once, shown during streaming
  fileName?: string           // for user messages with an attachment
  fileSheets?: FileSheet[]    // parsed preview sheets — lets user re-open preview by clicking the badge
  toolEvents?: ToolEvent[]    // for assistant messages
  streaming?: boolean
  validationWarnings?: ValidationWarning[]  // analyst mode: hallucination warnings
  fileAnnotation?: string              // file content injected into conversation history for follow-up turns
  validationNote?: string              // status line shown after a rule is saved and validated
  validationTransactions?: CsvParsedTransaction[]  // CSV/XLS: parsed rows for the table
  validationRawContent?: string        // decoded source file text for the raw view
  validationEmailFields?: TrialExtractedField[]    // email: extracted field values
  // Structured fields for the auto-feedback prompt — let the AI see specific
  // diagnostic detail (counts, rule values) rather than just the prose note.
  validationActualCount?: number
  validationExpectedCount?: number | null
  validationRule?: Record<string, unknown>  // the rule that was saved & validated
  validationFilename?: string
  validationAutoTriggered?: boolean    // true if askAssistantToFix was auto-fired
}

// ── State ─────────────────────────────────────────────────────────────────────

const llmConfigured = ref(true) // optimistic; corrected on mount
const messages = ref<DisplayMessage[]>([])
const inputText = ref('')
const attachedFile = ref<AttachedFile | null>(null)
const streaming = ref(false)
// AbortController for the active stream — replaced on each send, aborted on reset
const streamAbortController = ref<AbortController | null>(null)
// Kept across the send/clear cycle so the validator can use them after streaming ends
const sentFile = ref<AttachedFile | null>(null)
const sentExpectedCount = ref<number | null>(null)  // user-supplied expected transaction count
// Sidebar — supports file preview and recipe preview modes
const sidebarMode = ref<'none' | 'filePreview' | 'recipePreview'>('none')
const sidebarWidthPct = ref(55) // percentage of total width
const previewSheets = ref<FileSheet[] | null>(null)
const previewFileName = ref<string | null>(null)
const previewRecipe = ref<JsonDashboardRecipe | JsonWidgetRecipe | null>(null)
const previewRecipeType = ref<'widget' | 'dashboard' | null>(null)

const sidebarOpen = computed(() => sidebarMode.value !== 'none')

function closeSidebar() {
  sidebarMode.value = 'none'
  // Keep data in refs so re-opening is possible
}

// ── Resizable sidebar ────────────────────────────────────────────────────────

function startResize(e: MouseEvent) {
  e.preventDefault()
  const containerEl = (e.target as HTMLElement).parentElement
  if (!containerEl) return

  const containerRect = containerEl.getBoundingClientRect()
  const containerWidth = containerRect.width

  function onMouseMove(moveEvent: MouseEvent) {
    const relativeX = moveEvent.clientX - containerRect.left
    const chatPct = (relativeX / containerWidth) * 100
    // Clamp: chat pane min 25%, sidebar min 25%
    const clampedChatPct = Math.max(25, Math.min(75, chatPct))
    sidebarWidthPct.value = 100 - clampedChatPct
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
// Persistent tracking of the last successfully written rule (survives across turns)
const lastSavedRuleTool = ref<'write_csv_rule' | 'write_xls_rule' | 'write_email_rule' | null>(null)
const lastSavedRuleFilename = ref<string | null>(null)
// Mode set by the first file attachment in the conversation — persists across follow-up turns
// so the backend keeps receiving setup tools even after the file is cleared from the input.
const sessionMode = ref<'setup' | 'analyst'>('analyst')
const sessionFileType = ref<string | null>(null)  // 'csv' | 'xls' | 'email' | null

const textareaEl = ref<HTMLTextAreaElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
const messageListEl = ref<HTMLDivElement | null>(null)

// Rule quick-fill fields — shown when a CSV/XLS/EML file is attached
const ruleFilename = ref('')
const ruleAccount = ref('')
const ruleCurrency = ref('')
const expectedTxCount = ref<string>('')  // optional; used to validate parsed count (CSV/XLS only)

const showRuleFields = computed(() => {
  const name = attachedFile.value?.name.toLowerCase() ?? ''
  return name.endsWith('.csv') || name.endsWith('.xls') || name.endsWith('.xlsx') || name.endsWith('.xlsm') || name.endsWith('.eml')
})

const isEmailFile = computed(() => attachedFile.value?.name.toLowerCase().endsWith('.eml') ?? false)

function suggestRuleFilename(uploadedName: string): string {
  return uploadedName.replace(/\.[^.]+$/, '.yaml')
}

const canSend = computed(() =>
  !streaming.value && (inputText.value.trim().length > 0 || attachedFile.value !== null)
)

// ── LLM config check ──────────────────────────────────────────────────────────

const { loadConfig, config: appConfig } = useConfig()

onMounted(async () => {
  try {
    await loadConfig()
    llmConfigured.value = appConfig.value?.ai?.llm?.is_configured ?? false
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
    // Pre-populate rule fields and open preview for CSV/XLS/EML files
    const lower = file.name.toLowerCase()
    if (lower.endsWith('.csv') || lower.endsWith('.xls') || lower.endsWith('.xlsx') || lower.endsWith('.xlsm') || lower.endsWith('.eml')) {
      ruleFilename.value = suggestRuleFilename(file.name)
      ruleAccount.value = ''
      ruleCurrency.value = ''
    }
    if (lower.endsWith('.csv') || lower.endsWith('.xls') || lower.endsWith('.xlsx') || lower.endsWith('.xlsm')) {
      const sheets = parseFileForPreview(attachedFile.value)
      if (sheets.length > 0) {
        previewSheets.value = sheets
        previewFileName.value = file.name
        sidebarMode.value = 'filePreview'
      }
    }
  } catch (err) {
    console.error('Failed to read file', err)
  }
  // Reset input so the same file can be reselected
  input.value = ''
}

// ── Send message ──────────────────────────────────────────────────────────────

async function sendMessage() {
  if (!canSend.value) return

  // Build the final prompt, prepending rule quick-fill fields if set
  const parts: string[] = []
  if (showRuleFields.value && (ruleFilename.value.trim() || ruleAccount.value.trim() || ruleCurrency.value.trim())) {
    let intro = isEmailFile.value
      ? 'Create an email import rule for this email'
      : 'Create a rule file to parse this file'
    if (ruleFilename.value.trim()) intro += ` and save it as ${ruleFilename.value.trim()}`
    if (ruleAccount.value.trim())  intro += `. Beancount account is ${ruleAccount.value.trim()}`
    if (ruleCurrency.value.trim()) intro += `. Currency is ${ruleCurrency.value.trim()}`
    parts.push(intro + '.')
  }
  if (inputText.value.trim()) parts.push(inputText.value.trim())

  const text = parts.join(' ')
  const file = attachedFile.value

  if (!text && !file) return

  // Grab the already-parsed preview sheets for the message badge (parsed on file select)
  let fileSheets: FileSheet[] | undefined
  if (file && previewSheets.value && previewFileName.value === file.name) {
    fileSheets = previewSheets.value
  }

  // Build a file annotation so the file content survives in conversation history
  // across follow-up turns. The server injects this on the first turn only; on
  // subsequent turns the file is not re-sent, so we preserve it client-side.
  let fileAnnotation: string | undefined
  if (file) {
    const lower = file.name.toLowerCase()
    const isTextFile = lower.endsWith('.eml') || lower.endsWith('.csv') || lower.endsWith('.tsv') || lower.endsWith('.txt')
    if (isTextFile) {
      fileAnnotation = `\n\n--- Attached file: ${file.name} ---\n${base64ToText(file.content_base64)}`
    }
  }

  // Add user message to display (store sheets so the badge can re-open the preview)
  messages.value.push({
    role: 'user',
    content: text || `(attached: ${file!.name})`,
    fileName: file?.name,
    fileSheets,
    fileAnnotation,
  })

  inputText.value = ''
  ruleFilename.value = ''
  ruleAccount.value = ''
  ruleCurrency.value = ''
  const expectedCount = expectedTxCount.value ? parseInt(expectedTxCount.value, 10) : null
  if (file) {
    sentFile.value = file  // keep for post-stream validation; persist across turns
    sessionMode.value = 'setup'  // lock mode for the rest of the conversation
    // Detect file type for follow-up turns when the file isn't re-sent
    const lower = file.name.toLowerCase()
    if (lower.endsWith('.eml')) sessionFileType.value = 'email'
    else if (lower.endsWith('.csv') || lower.endsWith('.tsv') || lower.endsWith('.txt')) sessionFileType.value = 'csv'
    else if (/\.(xls|xlsx|xlsm|xlsb)$/i.test(file.name)) sessionFileType.value = 'xls'
  }
  if (expectedCount !== null) sentExpectedCount.value = expectedCount
  expectedTxCount.value = ''
  attachedFile.value = null
  resetTextareaHeight()
  scrollToBottom()

  // Build conversation history for the API.
  // For the CURRENT turn's user message (the last one we just pushed), do NOT
  // append the file annotation — the server will inject it from the attached file.
  // For PREVIOUS turns, re-inject the stored annotation so the LLM retains
  // file content across follow-up turns.
  const currentUserMsg = messages.value[messages.value.length - 1]
  const apiMessages: ChatMessage[] = messages.value
    .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.content))
    .map((m) => ({
      role: m.role,
      content: m !== currentUserMsg && m.fileAnnotation ? m.content + m.fileAnnotation : m.content,
    }))

  // Add a placeholder assistant message
  const assistantMsg: DisplayMessage = {
    role: 'assistant',
    content: '',
    toolEvents: [],
    streaming: true,
  }
  messages.value.push(assistantMsg)
  streaming.value = true

  const abortController = new AbortController()
  streamAbortController.value = abortController

  // Track whether a rule was saved this turn so we can validate after streaming
  // These local vars capture what happened this turn; persistent refs are the fallback
  let savedRuleTool: 'write_csv_rule' | 'write_xls_rule' | 'write_email_rule' | 'write_recipe' | null = null
  let savedRuleFilename: string | null = null
  // Relative recipe path (e.g. "dashboards/year-overview.json") captured from
  // write_recipe's manifest_entry field. Needed for the post-stream disk check
  // because savedRuleFilename is stripped to a basename and loses the
  // dashboards/ vs widgets/ subdir.
  let savedRecipePath: string | null = null

  try {
    const ctx: Record<string, string> = { page: 'assistant', mode: sessionMode.value }
    if (sessionFileType.value) ctx.file_type = sessionFileType.value
    for await (const event of streamAssistantChat(apiMessages, file, ctx, abortController.signal)) {
      if (event.type === 'thinking') {
        // Vue 3 reactivity quirk: properties added to a plain object after it
        // has been pushed into a ref array don't trigger updates when mutated
        // via the local raw reference. Mutate through messages.value[idx] (the
        // proxy) so re-renders fire. Same pattern used for validationNote etc.
        const m = messages.value[messages.value.length - 1]
        if (!m.thinking) m.thinking = ''
        m.thinking += event.content
        if (
          !m.thinkingSnippet
          && m.thinking.length >= THINKING_SNIPPET_CHARS
        ) {
          m.thinkingSnippet = m.thinking.slice(0, THINKING_SNIPPET_CHARS)
        }
      } else if (event.type === 'token') {
        assistantMsg.content += event.content
        scrollToBottom()
      } else if (event.type === 'tool_start') {
        assistantMsg.toolEvents!.push({
          tool: event.tool,
          message: event.message,
          done: false,
          success: true,
          args: event.args,
        })
        scrollToBottom()
      } else if (event.type === 'tool_result') {
        const te = assistantMsg.toolEvents!.find((t) => t.tool === event.tool && !t.done)
        if (te) {
          te.done = true
          te.success = event.success
          te.message = event.message
          te.data = event.data
        }
        // Handle recipe preview — show live widget or dashboard in sidebar
        if (event.tool === 'preview_recipe' && event.success && event.recipe) {
          try {
            const recipeType = event.recipe_type ?? 'dashboard'
            const resolved = resolveRecipeGenerators(
              event.recipe as unknown as JsonDashboardRecipe | JsonWidgetRecipe,
            )
            previewRecipe.value = resolved
            previewRecipeType.value = recipeType
            sidebarMode.value = 'recipePreview'
          } catch (err) {
            console.error('[preview] failed to resolve recipe generators:', err)
          }
        }
        // Track write tool calls so the post-stream fake-save guard can tell
        // a real save from a hallucinated one. Recipes (write_recipe) are
        // tracked here too even though they aren't validated against a sent
        // file — without this entry the heuristic mis-flags every dashboard
        // save as a fake.
        if (
          event.tool === 'write_csv_rule'
          || event.tool === 'write_xls_rule'
          || event.tool === 'write_email_rule'
          || event.tool === 'write_recipe'
        ) {
          savedRuleTool = event.tool
          const match = event.message.match(/`([^`]+)`/)
          if (match) {
            savedRuleFilename = match[1].split('/').pop() ?? null
          }
          // Recipe writes ship the relative path (e.g. "dashboards/foo.json")
          // in the structured payload — capture it for the post-stream disk
          // verification. Other write tools don't expose a comparable field.
          if (event.tool === 'write_recipe' && event.success) {
            const entry = event.data?.manifest_entry
            if (typeof entry === 'string' && entry.length > 0) {
              savedRecipePath = entry
            }
          }
          // Only update persistent refs on success so subsequent turns still have
          // a valid fallback even if a later write attempt fails. Skip recipes:
          // they don't drive the rule-vs-file validation flow.
          if (
            event.success
            && savedRuleFilename
            && savedRuleTool !== 'write_recipe'
          ) {
            lastSavedRuleTool.value = savedRuleTool
            lastSavedRuleFilename.value = savedRuleFilename
          }
        }
        scrollToBottom()
      } else if (event.type === 'validation_warning') {
        if (!assistantMsg.validationWarnings) assistantMsg.validationWarnings = []
        assistantMsg.validationWarnings.push({
          rule: event.rule,
          severity: event.severity,
          message: event.message,
          details: event.details,
          expanded: false,
        })
        scrollToBottom()
      } else if (event.type === 'error') {
        const link = event.docs_url ? ` [Learn more](${event.docs_url})` : ''
        assistantMsg.content += `\n\n**Error:** ${event.message}${link}`
        scrollToBottom()
      } else if (event.type === 'done') {
        break
      }
    }
  } catch (err: unknown) {
    // Don't append errors if chat was reset (messages cleared)
    if (messages.value.includes(assistantMsg)) {
      assistantMsg.content += `\n\n**Error:** ${err instanceof Error ? err.message : 'Unknown error'}`
    }
  } finally {
    assistantMsg.streaming = false
    streaming.value = false
    scrollToBottom()
    await nextTick()
    textareaEl.value?.focus()
  }

  // Validate the saved rule against the uploaded file.
  // If filename regex failed this turn but we know a rule was written, fall back to persistent refs.
  // Must set through messages.value[idx] (the reactive Proxy), not via the
  // local assistantMsg reference, so Vue detects the property change.
  // savedRuleTool is set whenever a write tool result was received this turn.
  // Fall back to the last known filename if the regex didn't match the message.
  const effectiveTool = savedRuleTool
  const effectiveFilename = savedRuleFilename ?? lastSavedRuleFilename.value
  // Only the three rule-writing tools drive file validation; write_recipe is
  // tracked above so the fake-save guard skips, but it has no file to validate.
  const ruleTool = effectiveTool && effectiveTool !== 'write_recipe' ? effectiveTool : null
  if (ruleTool && effectiveFilename && sentFile.value) {
    const result = await validateSavedRule(ruleTool, effectiveFilename, sentFile.value, sentExpectedCount.value)
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg) {
      lastMsg.validationNote = result.note
      lastMsg.validationTransactions = result.transactions
      lastMsg.validationRawContent = result.rawContent
      lastMsg.validationEmailFields = result.emailFields
      lastMsg.validationActualCount = result.actualCount
      lastMsg.validationExpectedCount = result.expectedCount
      lastMsg.validationRule = result.rule
      lastMsg.validationFilename = effectiveFilename
    }
    scrollToBottom()
    // Auto-feedback: when validation fires a *critical* failure (0 transactions
    // or a parse error), automatically ask the assistant to fix it instead of
    // waiting for the user to click the button. This closes the AI ↔ file
    // feedback loop that today depends on user attention. Cap at
    // MAX_AUTO_FIX_RETRIES per failure-streak so we don't infinite-loop on
    // unrecoverable errors (e.g. an XLS uploaded as CSV). On the first
    // successful save the counter resets, so a *new* rule attempt later in
    // the same conversation gets the full retry budget again.
    if (lastMsg && result.note && result.note.startsWith('✓')) {
      autoFixRetryCount.value = 0
    } else if (
      lastMsg
      && isCriticalValidationFailure(result.note)
      && autoFixRetryCount.value < MAX_AUTO_FIX_RETRIES
    ) {
      autoFixRetryCount.value += 1
      lastMsg.validationAutoTriggered = true
      // Tiny delay so the user sees the warning render before the next turn
      // starts streaming over it.
      setTimeout(() => askAssistantToFix(lastMsg), 600)
    }
  } else if (!effectiveTool && looksLikeFakeSaveClaim(assistantMsg.content)) {
    // Fake-save detection: the assistant's text claims a save happened, but no
    // write tool was actually invoked in this turn. Without this check, the
    // user has no signal that the file was not actually written.
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg) {
      lastMsg.validationNote = '⚠ The assistant claimed to save the rule, but the write tool was never invoked — the rule was NOT saved. Use the button below to ask the assistant to actually save it.'
    }
    scrollToBottom()
  } else if (effectiveTool === 'write_recipe' && savedRecipePath) {
    // Disk verification for recipe saves. The write_recipe tool reported
    // success, but we still cross-check that the file is actually retrievable
    // — if the manifest update or the persistence step failed silently, this
    // is the only signal the user gets.
    try {
      await RecipesService.getRecipeFileApiRecipesFilePathGet(savedRecipePath)
    } catch (err) {
      console.error('[validation] recipe disk check failed:', err)
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg) {
        lastMsg.validationNote = `⚠ The assistant reported saving \`${savedRecipePath}\`, but the file could not be loaded back from disk — the recipe may not have been written correctly. Use the button below to ask the assistant to retry.`
      }
      scrollToBottom()
    }
  }
}

// ── New chat ──────────────────────────────────────────────────────────────────

function resetChat() {
  // Abort any in-flight stream so the fetch is cancelled cleanly
  streamAbortController.value?.abort()
  streamAbortController.value = null

  messages.value = []
  inputText.value = ''
  attachedFile.value = null
  streaming.value = false
  sentFile.value = null
  sentExpectedCount.value = null
  sidebarMode.value = 'none'
  previewSheets.value = null
  previewFileName.value = null
  previewRecipe.value = null
  lastSavedRuleTool.value = null
  lastSavedRuleFilename.value = null
  autoFixRetryCount.value = 0
  sessionMode.value = 'analyst'
  sessionFileType.value = null
  ruleFilename.value = ''
  ruleAccount.value = ''
  ruleCurrency.value = ''
  expectedTxCount.value = ''
  resetTextareaHeight()
  nextTick(() => textareaEl.value?.focus())
}

// ── Rule validation ───────────────────────────────────────────────────────────

interface ValidationResult {
  note: string
  transactions: CsvParsedTransaction[]
  rawContent: string
  emailFields?: TrialExtractedField[]
  // Diagnostic detail for the auto-feedback prompt
  actualCount?: number
  expectedCount?: number | null
  rule?: Record<string, unknown>
}

// Cap auto-retries per conversation: avoid an AI ↔ validator infinite loop
// when the recipe simply can't parse the file (e.g. XLS uploaded with a CSV
// rule). Reset on resetChat() / new file upload.
const MAX_AUTO_FIX_RETRIES = 2
const autoFixRetryCount = ref(0)

async function validateSavedRule(
  tool: 'write_csv_rule' | 'write_xls_rule' | 'write_email_rule',
  filename: string,
  file: AttachedFile,
  expectedCount: number | null = null,
): Promise<ValidationResult> {
  const empty: ValidationResult = { note: '', transactions: [], rawContent: '' }
  const isXlsFile = /\.(xls|xlsx|xlsm|xlsb)$/i.test(file.name)

  try {
    if (tool === 'write_email_rule') {
      const emlContent = base64ToText(file.content_base64)
      const res = await ImportService.trialExtractEmail({ filename, eml_content: emlContent })
      const result = res.data
      if (!result) return empty
      return {
        note: result.note ?? '',
        transactions: [],
        rawContent: '',
        emailFields: result.fields ?? [],
      }
    } else if (tool === 'write_csv_rule') {
      // Safety net: if the file is XLS but the CSV tool was used, skip binary-as-text decoding
      if (isXlsFile) {
        return {
          note: `⚠ Rule was saved as a CSV rule but the file is XLS/XLSX. Please ask the assistant to recreate the rule using the correct tool.`,
          transactions: [],
          rawContent: '',
        }
      }
      const res = await ImportService.getCsvRule(filename)
      const rule = res.data
      if (!rule) return empty
      const text = base64ToText(file.content_base64)
      const transactions = parseCsvContent(text, rule)
      return {
        note: formatValidationNote(transactions.length, file.name, transactions[0]?.date, transactions.at(-1)?.date, expectedCount),
        transactions,
        rawContent: text,
        actualCount: transactions.length,
        expectedCount,
        rule: rule as unknown as Record<string, unknown>,
      }
    } else {
      const res = await ImportService.getXlsRule(filename)
      const rule = res.data
      if (!rule) return empty
      const buffer = base64ToArrayBuffer(file.content_base64)
      const transactions = parseXlsContent(buffer, rule)
      return {
        note: formatValidationNote(transactions.length, file.name, transactions[0]?.date, transactions.at(-1)?.date, expectedCount),
        transactions,
        rawContent: extractXlsText(buffer, rule),
        actualCount: transactions.length,
        expectedCount,
        rule: rule as unknown as Record<string, unknown>,
      }
    }
  } catch (err) {
    console.error('[validation] error:', err)
    const msg = err instanceof Error ? err.message : String(err)
    return {
      note: `⚠ Rule was saved but could not be validated against ${file.name}: ${msg}. The rule may be incorrect.`,
      transactions: [],
      rawContent: '',
    }
  }
}

function formatValidationNote(count: number, filename: string, first?: string, last?: string, expected: number | null = null): string {
  const range = first && last && first !== last ? ` (${first} → ${last})` : first ? ` (${first})` : ''
  if (count === 0) {
    return `⚠ Rule validated against ${filename}: found 0 transactions. The skip counts or date format may need adjustment — let me know the expected number and I'll fix it.`
  }
  if (expected !== null && count !== expected) {
    return `⚠ Rule validated against ${filename}: found ${count} transaction${count !== 1 ? 's' : ''}${range}, but expected ${expected}. Some transactions may be missing — the skip counts or date format may need adjustment.`
  }
  const countStr = expected !== null ? `${count}/${expected}` : `${count}`
  return `✓ Rule validated against ${filename}: found ${countStr} transaction${count !== 1 ? 's' : ''}${range}.`
}

// ── Validation display helpers ─────────────────────────────────────────────────

const MAX_VALIDATION_ROWS = 15
const MAX_RAW_LINES = 60

/** True for the most severe validation outcomes (parse error, 0 transactions, fake save, recipe missing on disk). */
function isCriticalValidationFailure(note: string): boolean {
  return /found 0 transaction|could not be validated|could not be loaded back from disk|was NOT saved/i.test(note)
}

/**
 * True when the assistant's text claims a completed save but no write tool was invoked.
 * Patterns are conservative — designed to fire on unambiguous past-tense / completion
 * claims and to NOT fire on future-tense or hypothetical save mentions like "I'll save",
 * "click save", or "before I save".
 */
function looksLikeFakeSaveClaim(content: string): boolean {
  const patterns = [
    // "the rule has been saved/updated/written"
    /\b(rule|file|configuration|yaml)\s+(has\s+been|have\s+been|was|is)\s+(saved|written|updated|created|stored)\b/i,
    // "I've saved" / "I have saved"
    /\bI\s*'?\s*(ve|have)\s+(saved|written|updated|created|stored)\b/i,
    // "successfully saved/updated"
    /\b(successfully|already)\s+(saved|written|updated|created)\b/i,
    // "Saving:Done!" or "Saving... done!"
    /\b(saving|writing|updating)[^.]*[:.\s]\s*done!?/i,
    // "Done. The rule..."
    /\bdone!?\.?\s+(the|your)\s+(rule|file|configuration)/i,
  ]
  return patterns.some(p => p.test(content))
}

/**
 * Pre-fill the chat input with a fix request based on the validation note,
 * then submit it. Builds a *specific* corrective prompt with the actual
 * count, expected count, and the rule's current key fields — the AI fixes
 * faster when it sees the diagnostic detail rather than a generic 'review
 * the rule' nudge.
 *
 * Accepts either a DisplayMessage (preferred — has rich diagnostics) or a
 * plain note string (legacy callers).
 */
function askAssistantToFix(arg: DisplayMessage | string) {
  if (streaming.value) return
  const note = typeof arg === 'string' ? arg : (arg.validationNote || '')
  const msg = typeof arg === 'string' ? null : arg

  if (/was NOT saved/i.test(note)) {
    inputText.value = "You claimed to save the rule, but the write tool was never invoked and the file was not actually saved. Please call the appropriate write tool now to save the rule."
  } else if (/could not be loaded back from disk/i.test(note)) {
    inputText.value = "The recipe was reported as saved but the file is not retrievable from disk. Please call write_recipe again with the same content to save it properly."
  } else if (isCriticalValidationFailure(note)) {
    inputText.value = buildCriticalFixPrompt(note, msg)
  } else {
    inputText.value = buildCountMismatchFixPrompt(note, msg)
  }
  void sendMessage()
}

/** Detailed fix prompt for parser failures / 0-transaction saves. */
function buildCriticalFixPrompt(note: string, msg: DisplayMessage | null): string {
  const parts: string[] = []
  parts.push(
    `The rule you just saved failed validation against the user's file. ` +
    `Result: ${note.replace(/^⚠\s*/, '').trim()}`
  )
  if (msg?.validationFilename) parts.push(`Saved rule filename: ${msg.validationFilename}.`)
  const ruleSummary = summariseRuleForFix(msg?.validationRule)
  if (ruleSummary) parts.push(`Current rule values that may be wrong:\n${ruleSummary}`)
  parts.push(
    `Diagnose why the rule didn't parse the file correctly — common causes: ` +
    `wrong skip_lines_start, wrong date_format, swapped column indices, ` +
    `wrong sheet_name (XLS), missing rows between header and first transaction. ` +
    `Read the rule with read_file if helpful. Fix and save again.`
  )
  return parts.join('\n\n')
}

/** Less alarming prompt for count-mismatch (rule works, count differs). */
function buildCountMismatchFixPrompt(note: string, msg: DisplayMessage | null): string {
  const parts: string[] = [`The saved rule's transaction count doesn't match what's expected. ${note.replace(/^⚠\s*/, '').trim()}`]
  if (msg?.validationActualCount !== undefined && msg?.validationExpectedCount != null) {
    parts.push(`Found ${msg.validationActualCount} transactions; expected ${msg.validationExpectedCount}.`)
  }
  const ruleSummary = summariseRuleForFix(msg?.validationRule)
  if (ruleSummary) parts.push(`Current rule values:\n${ruleSummary}`)
  parts.push(
    `Some transactions are likely being missed. Common causes: skip_lines_end ` +
    `is too large (over-trimming the footer), skip_lines_start is too large ` +
    `(over-trimming the header), or amount_debit/amount_credit indices map to ` +
    `the wrong columns so some rows return amount=0 and get filtered out. ` +
    `Diagnose, fix, and save again.`
  )
  return parts.join('\n\n')
}

/** Render a compact subset of a saved rule's fields for inclusion in a fix
 *  prompt. Picks the fields most often responsible for parse failures. */
function summariseRuleForFix(rule: Record<string, unknown> | undefined): string {
  if (!rule) return ''
  const lines: string[] = []
  const fields: Array<keyof typeof rule | string> = [
    'skip_lines_start', 'skip_lines_end', 'date_format', 'decimal_separator',
    'separator', 'sheet_index', 'sheet_name', 'columns', 'default_account',
    'default_currency',
  ]
  for (const f of fields) {
    if (rule[f] !== undefined) {
      const v = rule[f]
      const formatted = typeof v === 'object' ? JSON.stringify(v) : String(v)
      lines.push(`  - ${f}: ${formatted}`)
    }
  }
  return lines.join('\n')
}

function validationRows(txs: CsvParsedTransaction[]): CsvParsedTransaction[] {
  return txs.slice(0, MAX_VALIDATION_ROWS)
}

function hasDescription(txs: CsvParsedTransaction[]): boolean {
  return txs.some(tx => tx.payee || tx.narration || tx.memo)
}

function txDescription(tx: CsvParsedTransaction): string {
  return tx.payee || tx.narration || tx.memo || ''
}

function formatAmount(amount: Money): string {
  const n = toNumber(amount)
  const s = n >= 0 ? '+' : ''
  return s + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rawContentLines(content: string): string[] {
  return content.split('\n').slice(0, MAX_RAW_LINES)
}

function rawContentOverflow(content: string): number {
  return Math.max(0, content.split('\n').length - MAX_RAW_LINES)
}

function parseFileForPreview(file: AttachedFile): FileSheet[] {
  const lower = file.name.toLowerCase()
  try {
    if (lower.endsWith('.xls') || lower.endsWith('.xlsx') || lower.endsWith('.xlsm') || lower.endsWith('.xlsb')) {
      const buffer = base64ToArrayBuffer(file.content_base64)
      return extractXlsSheets(buffer)
    }
    if (lower.endsWith('.csv') || lower.endsWith('.tsv') || lower.endsWith('.txt')) {
      const text = base64ToText(file.content_base64)
      return [{ name: file.name, rows: extractCsvRows(text) }]
    }
  } catch (err) {
    console.error('[preview] failed to parse file for preview:', err)
  }
  return []
}

function base64ToText(b64: string): string {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  return bytes.buffer
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

// ── Validation warning helpers ────────────────────────────────────────────────

/**
 * Returns true if this warning has detail data worth showing beyond the message.
 *   unknown_account  → full account list when the message was truncated (>5)
 *   date_out_of_range → full date list when the message was truncated (>3)
 *   amount_mismatch  → always shows the max query value for context
 */
function warningHasExpandableDetails(warning: ValidationWarning): boolean {
  if (!warning.details) return false
  if (warning.rule === 'unknown_account') {
    return ((warning.details.unknown_accounts as string[]) ?? []).length > 5
  }
  if (warning.rule === 'date_out_of_range') {
    return ((warning.details.out_of_range as string[]) ?? []).length > 3
  }
  if (warning.rule === 'amount_mismatch') {
    return 'max_query_value' in warning.details
  }
  return false
}

// ── Markdown renderer ─────────────────────────────────────────────────────────

function _isPipeRow(line: string): boolean {
  const t = line.trim()
  return t.startsWith('|') && t.endsWith('|') && t.length > 1
}

function _isSepRow(line: string): boolean {
  // |---|---| or |:---|:---:| etc.
  return /^\s*\|[\s\-:|]+\|\s*$/.test(line)
}

function _renderTableBlock(lines: string[]): string {
  const parseRow = (line: string) =>
    line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())

  const sepIdx = lines.findIndex((l) => _isSepRow(l))
  if (sepIdx < 1) return _renderInlineText(lines.join('\n'))

  const headers = parseRow(lines[0])
  const rows = lines.slice(sepIdx + 1).filter((l) => l.trim()).map(parseRow)

  const th = headers
    .map(
      (h) =>
        `<th class="px-3 py-2 text-left text-xs font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide border-b border-gray-200 dark:border-white/10 whitespace-nowrap">${h}</th>`,
    )
    .join('')

  const trs = rows
    .map((cells, idx) => {
      const tds = cells
        .map(
          (c) =>
            `<td class="px-3 py-1.5 text-gray-800 dark:text-gray-200 border-b border-gray-100 dark:border-white/10/50">${c}</td>`,
        )
        .join('')
      const bg = idx % 2 === 1 ? ' class="bg-gray-50/50 dark:bg-white/5"' : ''
      return `<tr${bg}>${tds}</tr>`
    })
    .join('')

  return (
    `<div class="overflow-x-auto my-2 rounded-lg border border-gray-200 dark:border-white/10 text-sm">` +
    `<table class="min-w-full border-collapse">` +
    `<thead><tr class="bg-gray-50 dark:bg-gray-800/75">${th}</tr></thead>` +
    `<tbody>${trs}</tbody>` +
    `</table></div>`
  )
}

function _renderInlineText(text: string): string {
  if (!text.trim()) return ''
  return text
    .replace(
      /```(\w*)\n([\s\S]*?)```/g,
      '<pre class="overflow-x-auto rounded p-2 text-xs"><code>$2</code></pre>',
    )
    .replace(
      /`([^`]+)`/g,
      '<code class="rounded bg-gray-100 dark:bg-gray-400/10 px-1 py-0.5 text-xs font-mono">$1</code>',
    )
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="text-indigo-600 hover:underline dark:text-indigo-400">$1</a>',
    )
    .replace(/\n\n/g, '</p><p class="mt-2">')
    .replace(/\n/g, '<br/>')
    .replace(/^/, '<p>')
    .replace(/$/, '</p>')
}

/**
 * Renders markdown to HTML.  Processes table blocks before applying inline
 * transforms so that pipe characters aren't mangled by the paragraph/newline
 * replacements.
 */
function renderMarkdown(text: string): string {
  const lines = text.split('\n')
  const segments: string[] = []
  const textBuf: string[] = []
  let i = 0

  function flushText() {
    if (textBuf.length > 0) {
      segments.push(_renderInlineText(textBuf.join('\n')))
      textBuf.length = 0
    }
  }

  while (i < lines.length) {
    // A table block begins when a pipe row is followed by a separator row
    if (_isPipeRow(lines[i]) && i + 1 < lines.length && _isSepRow(lines[i + 1])) {
      flushText()
      const tableLines: string[] = []
      while (i < lines.length && _isPipeRow(lines[i])) {
        tableLines.push(lines[i++])
      }
      segments.push(_renderTableBlock(tableLines))
    } else {
      textBuf.push(lines[i++])
    }
  }
  flushText()

  return segments.join('')
}

function formatToolData(data: Record<string, unknown>): string {
  return JSON.stringify(data, null, 2)
}
</script>
