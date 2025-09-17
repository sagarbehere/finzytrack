<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="mt-1 text-gray-600 dark:text-gray-400">Application preferences and configuration</p>
    </div>

    <div class="space-y-6">
      <!-- Theme settings -->
      <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
        <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Appearance</h3>
          <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
            Customize the look and feel of the application
          </p>
        </div>
        <div class="p-6">
          <div class="space-y-4">
            <div>
              <label class="text-base font-medium text-gray-900 dark:text-white"> Theme </label>
              <p class="text-sm text-gray-500 dark:text-gray-400">
                Choose your preferred color scheme
              </p>
              <fieldset class="mt-4">
                <legend class="sr-only">Theme selection</legend>
                <div class="space-y-4">
                  <div class="flex items-center">
                    <input
                      id="theme-system"
                      name="theme"
                      type="radio"
                      checked
                      class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                    />
                    <label
                      for="theme-system"
                      class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                    >
                      System (Auto)
                    </label>
                  </div>
                  <div class="flex items-center">
                    <input
                      id="theme-light"
                      name="theme"
                      type="radio"
                      class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                    />
                    <label
                      for="theme-light"
                      class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                    >
                      Light
                    </label>
                  </div>
                  <div class="flex items-center">
                    <input
                      id="theme-dark"
                      name="theme"
                      type="radio"
                      class="focus:ring-blue-500 h-4 w-4 text-blue-600 border-gray-300"
                    />
                    <label
                      for="theme-dark"
                      class="ml-3 block text-sm font-medium text-gray-700 dark:text-gray-300"
                    >
                      Dark
                    </label>
                  </div>
                </div>
              </fieldset>
            </div>
          </div>
        </div>
      </div>

      <!-- File settings -->
      <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
        <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Files & Data</h3>
          <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
            Manage your ledger files and data settings
          </p>
        </div>
        <div class="p-6">
          <div class="text-center py-8">
            <div class="text-gray-400 dark:text-gray-500 text-4xl mb-4">⚙️</div>
            <p class="text-gray-500 dark:text-gray-400">
              File and data configuration options coming soon
            </p>
          </div>
        </div>
      </div>

      <!-- Import settings -->
      <div class="bg-white dark:bg-gray-800 shadow rounded-lg border dark:border-gray-700">
        <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Import Rules</h3>
          <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
            Configure automatic categorization and account mappings
          </p>
        </div>
        <div class="p-6">
          <div class="text-center py-8">
            <div class="text-gray-400 dark:text-gray-500 text-4xl mb-4">🔄</div>
            <p class="text-gray-500 dark:text-gray-400">
              Import rules and mappings configuration coming soon
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
  // Settings view component placeholder
</script>
