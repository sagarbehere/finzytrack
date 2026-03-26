<template>
  <div class="flex flex-col h-full">
    <!-- Page header -->
    <div class="mb-4 flex-none flex items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">AI Assistant</h1>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
          AI is experimental and can make mistakes. Review all outputs carefully.
          <a href="#" class="ml-1 text-indigo-500 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 underline underline-offset-2">What data is shared with the AI model?</a>
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

                <!-- Status line -->
                <div
                  class="rounded-lg px-3 py-2 text-xs font-medium"
                  :class="msg.validationNote.startsWith('✓')
                    ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                    : 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400'"
                >{{ msg.validationNote }}</div>

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
                          :class="tx.amount >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'"
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
            <RecipeDashboard :dashboard="previewRecipe" />
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
            <input
              v-model="ruleAccount"
              type="text"
              placeholder="Assets:Bank:Name"
              class="w-full rounded-md bg-white px-2 py-1.5 text-xs text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
            />
          </div>
          <div class="w-24">
            <label class="block text-xs text-gray-400 dark:text-gray-500 mb-0.5">Currency</label>
            <input
              v-model="ruleCurrency"
              type="text"
              placeholder="INR"
              class="w-full rounded-md bg-white px-2 py-1.5 text-xs text-gray-900 outline-1 -outline-offset-1 outline-gray-300 placeholder:text-gray-400 focus:outline-2 focus:-outline-offset-2 focus:outline-indigo-600 dark:bg-white/5 dark:text-white dark:outline-white/10 dark:focus:outline-indigo-500"
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
import { ImportService } from '@/services/generated-api'
import { parseCsvContent, extractCsvRows } from '@/composables/useCsvParser'
import { parseXlsContent, extractXlsText, extractXlsSheets } from '@/composables/useXlsParser'
import type { CsvParsedTransaction } from '@/types/csv'
import FilePreviewTable from '@/components/FilePreviewTable.vue'
import RecipeDashboard from '@/components/recipes/RecipeDashboard.vue'
import type { JsonDashboardRecipe } from '@/types/recipes'
import { resolveGenerators } from '@/recipes/functions'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ToolEvent {
  tool: string
  message: string
  done: boolean
  success: boolean
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

interface DisplayMessage {
  role: 'user' | 'assistant'
  content: string
  fileName?: string           // for user messages with an attachment
  fileSheets?: FileSheet[]    // parsed preview sheets — lets user re-open preview by clicking the badge
  toolEvents?: ToolEvent[]    // for assistant messages
  streaming?: boolean
  validationWarnings?: ValidationWarning[]  // analyst mode: hallucination warnings
  validationNote?: string              // status line shown after a rule is saved and validated
  validationTransactions?: CsvParsedTransaction[]  // parsed rows for the table
  validationRawContent?: string        // decoded source file text for the raw view
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
const previewRecipe = ref<JsonDashboardRecipe | null>(null)

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
const lastSavedRuleTool = ref<'write_csv_rule' | 'write_xls_rule' | null>(null)
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

  // Add user message to display (store sheets so the badge can re-open the preview)
  messages.value.push({
    role: 'user',
    content: text || `(attached: ${file!.name})`,
    fileName: file?.name,
    fileSheets,
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

  const abortController = new AbortController()
  streamAbortController.value = abortController

  // Track whether a rule was saved this turn so we can validate after streaming
  // These local vars capture what happened this turn; persistent refs are the fallback
  let savedRuleTool: 'write_csv_rule' | 'write_xls_rule' | null = null
  let savedRuleFilename: string | null = null
  let ruleWrittenThisTurn = false

  try {
    const ctx: Record<string, string> = { page: 'assistant', mode: sessionMode.value }
    if (sessionFileType.value) ctx.file_type = sessionFileType.value
    for await (const event of streamAssistantChat(apiMessages, file, ctx, abortController.signal)) {
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
        // Handle recipe preview — show live dashboard in sidebar
        if (event.tool === 'preview_recipe' && event.success && event.recipe) {
          try {
            const resolved = resolveGenerators(event.recipe as unknown as JsonDashboardRecipe)
            previewRecipe.value = resolved
            sidebarMode.value = 'recipePreview'
          } catch (err) {
            console.error('[preview] failed to resolve recipe generators:', err)
          }
        }
        // Track write tool calls for post-stream validation.
        // Always set ruleWrittenThisTurn regardless of success, so we validate
        // even after a failed write (shows current on-disk rule state to the user).
        if (event.tool === 'write_csv_rule' || event.tool === 'write_xls_rule') {
          ruleWrittenThisTurn = true
          savedRuleTool = event.tool
          const match = event.message.match(/`([^`]+)`/)
          if (match) {
            savedRuleFilename = match[1].split('/').pop() ?? null
          }
          // Only update persistent refs on success so subsequent turns still have
          // a valid fallback even if a later write attempt fails.
          if (event.success && savedRuleFilename) {
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
        assistantMsg.content += `\n\n**Error:** ${event.message}`
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
  if (effectiveTool && effectiveFilename && sentFile.value) {
    const { note, transactions, rawContent } = await validateSavedRule(effectiveTool, effectiveFilename, sentFile.value, sentExpectedCount.value)
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg) {
      lastMsg.validationNote = note
      lastMsg.validationTransactions = transactions
      lastMsg.validationRawContent = rawContent
    }
    scrollToBottom()
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
}

async function validateSavedRule(
  tool: 'write_csv_rule' | 'write_xls_rule',
  filename: string,
  file: AttachedFile,
  expectedCount: number | null = null,
): Promise<ValidationResult> {
  const empty: ValidationResult = { note: '', transactions: [], rawContent: '' }
  const isXlsFile = /\.(xls|xlsx|xlsm|xlsb)$/i.test(file.name)

  try {
    if (tool === 'write_csv_rule') {
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
      }
    }
  } catch (err) {
    console.error('[validation] error:', err)
    return empty
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

function validationRows(txs: CsvParsedTransaction[]): CsvParsedTransaction[] {
  return txs.slice(0, MAX_VALIDATION_ROWS)
}

function hasDescription(txs: CsvParsedTransaction[]): boolean {
  return txs.some(tx => tx.payee || tx.narration || tx.memo)
}

function txDescription(tx: CsvParsedTransaction): string {
  return tx.payee || tx.narration || tx.memo || ''
}

function formatAmount(amount: number): string {
  const sign = amount >= 0 ? '+' : ''
  return sign + amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
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
</script>
