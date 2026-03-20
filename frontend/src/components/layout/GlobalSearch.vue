<template>
  <div class="relative flex-1 self-stretch" ref="containerRef">
    <div class="grid grid-cols-1 h-full">
      <input
        ref="inputRef"
        v-model="query"
        type="text"
        aria-label="Search"
        placeholder="Search transactions, accounts..."
        class="col-start-1 row-start-1 block size-full bg-white pl-8 text-base text-gray-900 outline-none placeholder:text-gray-400 sm:text-sm/6 dark:bg-gray-900 dark:text-white dark:placeholder:text-gray-500"
        @input="onInput"
        @keydown="onKeydown"
        @focus="onFocus"
      />
      <MagnifyingGlassIcon
        class="pointer-events-none col-start-1 row-start-1 size-5 self-center text-gray-400"
        aria-hidden="true"
      />
    </div>

    <!-- Dropdown -->
    <div
      v-if="showDropdown"
      class="absolute left-0 right-0 top-full mt-1 bg-white dark:bg-gray-800 rounded-md shadow-lg ring-1 ring-black ring-opacity-5 z-50 overflow-hidden"
    >
      <!-- Matching accounts -->
      <div v-if="matchingAccounts.length > 0">
        <div class="px-3 py-1.5 text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide border-b border-gray-100 dark:border-gray-700">
          Accounts
        </div>
        <button
          v-for="(account, i) in matchingAccounts"
          :key="account"
          @mousedown.prevent="selectAccount(account)"
          :class="[
            'w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-gray-50 dark:hover:bg-gray-700',
            activeIndex === i ? 'bg-gray-50 dark:bg-gray-700' : '',
          ]"
        >
          <BuildingLibraryIcon class="h-4 w-4 text-gray-400 flex-shrink-0" />
          <span class="text-gray-900 dark:text-white font-mono text-xs">{{ account }}</span>
        </button>
      </div>

      <!-- Search transactions option -->
      <button
        @mousedown.prevent="selectTransactionSearch"
        :class="[
          'w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-gray-50 dark:hover:bg-gray-700 border-t border-gray-100 dark:border-gray-700',
          activeIndex === matchingAccounts.length ? 'bg-gray-50 dark:bg-gray-700' : '',
        ]"
      >
        <MagnifyingGlassIcon class="h-4 w-4 text-gray-400 flex-shrink-0" />
        <span class="text-gray-900 dark:text-white">
          Search transactions for <span class="font-medium">"{{ query }}"</span>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup>
  import { ref, computed, onMounted, onUnmounted } from 'vue'
  import { useRouter } from 'vue-router'
  import { MagnifyingGlassIcon, BuildingLibraryIcon } from '@heroicons/vue/24/outline'
  import { useAccounts } from '@/composables/useAccounts'

  const router = useRouter()
  const { accountNames, fetchAccounts, hasBeenFetched } = useAccounts()

  const query = ref('')
  const showDropdown = ref(false)
  const activeIndex = ref(-1)
  const inputRef = ref(null)
  const containerRef = ref(null)

  onMounted(() => {
    if (!hasBeenFetched.value) fetchAccounts()
    document.addEventListener('mousedown', handleClickOutside)
  })

  onUnmounted(() => {
    document.removeEventListener('mousedown', handleClickOutside)
  })

  const matchingAccounts = computed(() => {
    if (!query.value.trim()) return []
    const term = query.value.toLowerCase()
    return accountNames.value
      .filter((name) => name.toLowerCase().includes(term))
      .slice(0, 6)
  })

  const totalItems = computed(() => matchingAccounts.value.length + 1) // +1 for transaction search

  function onInput() {
    activeIndex.value = -1
    showDropdown.value = query.value.trim().length > 0
  }

  function onFocus() {
    if (query.value.trim().length > 0) showDropdown.value = true
  }

  function onKeydown(e) {
    if (!showDropdown.value) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      activeIndex.value = (activeIndex.value + 1) % totalItems.value
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      activeIndex.value = (activeIndex.value - 1 + totalItems.value) % totalItems.value
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIndex.value >= 0 && activeIndex.value < matchingAccounts.value.length) {
        selectAccount(matchingAccounts.value[activeIndex.value])
      } else {
        selectTransactionSearch()
      }
    } else if (e.key === 'Escape') {
      closeDropdown()
    }
  }

  function selectAccount(account) {
    router.push({ path: '/accounts', query: { search: account } })
    closeDropdown()
  }

  function selectTransactionSearch() {
    if (!query.value.trim()) return
    router.push({ path: '/transactions', query: { search: query.value.trim() } })
    closeDropdown()
  }

  function closeDropdown() {
    showDropdown.value = false
    activeIndex.value = -1
    query.value = ''
    inputRef.value?.blur()
  }

  function handleClickOutside(e) {
    if (containerRef.value && !containerRef.value.contains(e.target)) {
      showDropdown.value = false
      activeIndex.value = -1
    }
  }
</script>
