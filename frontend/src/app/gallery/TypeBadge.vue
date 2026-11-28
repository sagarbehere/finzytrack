<script setup lang="ts">
// An account-type badge (design/specs/visual-language.md, "Badges"): one tone per Beancount account type. The type
// comes from the backend's account data, never from an account's name (ADR-0024). Specimen for the gallery; the
// accounts vertical makes it a components/app primitive.
export type AccountType = 'assets' | 'liabilities' | 'equity' | 'income' | 'expenses'

defineProps<{ type: AccountType }>()

const TONES: Record<AccountType, string> = {
  assets: 'bg-badge-assets text-badge-assets-foreground',
  liabilities: 'bg-badge-liabilities text-badge-liabilities-foreground',
  equity: 'bg-badge-equity text-badge-equity-foreground',
  income: 'bg-badge-income text-badge-income-foreground',
  expenses: 'bg-badge-expenses text-badge-expenses-foreground',
}
</script>

<template>
  <span class="rounded-full px-2.25 py-0.5 text-badge font-semibold" :class="TONES[type]"><slot /></span>
</template>
