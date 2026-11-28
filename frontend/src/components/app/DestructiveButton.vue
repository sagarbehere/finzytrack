<script setup lang="ts">
// The button for an action that deletes or discards (BL-0325; owner, 2026-11-28: "App-level override
// (Recommended)"). It is the vendored destructive button with the app's one focus ring: the copy draws its ring as
// `ring-destructive/20` (light) and `/40` (dark), which no colour can bring to 3:1, and `cn` lets our class win.
import type { HTMLAttributes } from 'vue'

import { cn } from 'cn'

import { Button, type ButtonVariants } from '../ui/button'

const props = defineProps<{ size?: ButtonVariants['size']; class?: HTMLAttributes['class'] }>()

const FOCUS_RING = 'focus-visible:ring-ring/50 dark:focus-visible:ring-ring/50'
</script>

<template>
  <Button variant="destructive" :size="props.size" :class="cn(FOCUS_RING, props.class)"><slot /></Button>
</template>
