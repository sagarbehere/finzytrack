<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Overview of your financial status</p>
    </div>

    <!-- Dashboard content placeholder -->
    <div class="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <!-- Summary cards -->
      <div class="lg:col-span-3">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div
            class="bg-white dark:bg-gray-800 overflow-hidden shadow rounded-lg border dark:border-gray-700"
          >
            <div class="p-5">
              <div class="flex items-center">
                <div class="flex-shrink-0">
                  <div class="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center">
                    <span class="text-white text-sm font-semibold">$</span>
                  </div>
                </div>
                <div class="ml-5 w-0 flex-1">
                  <dl>
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
                      Total Balance
                    </dt>
                    <dd class="text-lg font-semibold text-gray-900 dark:text-white">$12,345.67</dd>
                  </dl>
                </div>
              </div>
            </div>
          </div>

          <div
            class="bg-white dark:bg-gray-800 overflow-hidden shadow rounded-lg border dark:border-gray-700"
          >
            <div class="p-5">
              <div class="flex items-center">
                <div class="flex-shrink-0">
                  <div class="w-8 h-8 bg-green-500 rounded-full flex items-center justify-center">
                    <span class="text-white text-sm font-semibold">↑</span>
                  </div>
                </div>
                <div class="ml-5 w-0 flex-1">
                  <dl>
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
                      This Month Income
                    </dt>
                    <dd class="text-lg font-semibold text-gray-900 dark:text-white">$5,234.00</dd>
                  </dl>
                </div>
              </div>
            </div>
          </div>

          <div
            class="bg-white dark:bg-gray-800 overflow-hidden shadow rounded-lg border dark:border-gray-700"
          >
            <div class="p-5">
              <div class="flex items-center">
                <div class="flex-shrink-0">
                  <div class="w-8 h-8 bg-red-500 rounded-full flex items-center justify-center">
                    <span class="text-white text-sm font-semibold">↓</span>
                  </div>
                </div>
                <div class="ml-5 w-0 flex-1">
                  <dl>
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
                      This Month Expenses
                    </dt>
                    <dd class="text-lg font-semibold text-gray-900 dark:text-white">$3,456.78</dd>
                  </dl>
                </div>
              </div>
            </div>
          </div>

          <div
            class="bg-white dark:bg-gray-800 overflow-hidden shadow rounded-lg border dark:border-gray-700"
          >
            <div class="p-5">
              <div class="flex items-center">
                <div class="flex-shrink-0">
                  <div class="w-8 h-8 bg-purple-500 rounded-full flex items-center justify-center">
                    <span class="text-white text-sm font-semibold">#</span>
                  </div>
                </div>
                <div class="ml-5 w-0 flex-1">
                  <dl>
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
                      Transactions
                    </dt>
                    <dd class="text-lg font-semibold text-gray-900 dark:text-white">127</dd>
                  </dl>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Recent transactions placeholder -->
      <div class="lg:col-span-2">
        <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">Recent Transactions</h3>
          </div>
          <div class="p-6">
            <div class="text-center py-8">
              <div class="text-gray-400 dark:text-gray-500 text-4xl mb-4">📊</div>
              <p class="text-gray-500 dark:text-gray-400">Recent transactions will appear here</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Account balances placeholder -->
      <div class="lg:col-span-1">
        <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">Account Balances</h3>
          </div>
          <div class="p-6">
            <div class="text-center py-8">
              <div class="text-gray-400 dark:text-gray-500 text-4xl mb-4">🏦</div>
              <p class="text-gray-500 dark:text-gray-400">Account balances will appear here</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
  // Dashboard view component
  // This is a placeholder that will be enhanced with real data and visualizations
</script>
