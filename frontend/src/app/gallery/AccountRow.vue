<script setup lang="ts">
// One row of the accounts table specimen (design/specs/visual-language.md, "Tables"): the name indented by depth,
// a chevron for a parent, the type badge on a root row, the currencies, and the balance as one link per currency
// (owner, 2026-11-28: "the balances ARE links"), neutral with a dotted underline that turns indigo on hover.
import { ChevronDown, ChevronRight } from '@lucide/vue'

import TypeBadge, { type AccountType } from './TypeBadge.vue'

const props = defineProps<{
  name: string
  depth: 0 | 1 | 2
  open?: boolean
  type?: AccountType
  typeLabel?: string
  currencies: string
  balances: string[]
}>()

const INDENT = ['pl-5.5 font-semibold', 'pl-11', 'pl-16.5'] as const
</script>

<template>
  <tr class="border-t border-hairline">
    <td class="py-cell-y pr-3" :class="INDENT[props.depth]">
      <span class="inline-flex items-center gap-1.5">
        <span class="w-3.5 text-muted-foreground">
          <template v-if="props.open !== undefined">
            <ChevronDown v-if="props.open" class="size-3.5" />
            <ChevronRight v-else class="size-3.5" />
          </template>
        </span>
        {{ props.name }}
      </span>
    </td>
    <td class="px-3 py-cell-y">
      <TypeBadge v-if="props.type" :type="props.type">{{ props.typeLabel }}</TypeBadge>
    </td>
    <td class="px-3 py-cell-y text-caption text-muted-foreground">{{ props.currencies }}</td>
    <td class="py-cell-y pr-5.5 pl-3 text-right whitespace-nowrap">
      <span class="flex flex-col items-end gap-px">
        <a
          v-for="balance in props.balances"
          :key="balance"
          href="#transactions"
          class="underline decoration-link-underline decoration-dotted underline-offset-4 hover:text-primary hover:decoration-current hover:decoration-solid"
          :class="props.depth === 0 ? 'font-semibold' : 'font-medium'"
          >{{ balance }}</a
        >
      </span>
    </td>
  </tr>
</template>
