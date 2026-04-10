import { ref, onMounted, onUnmounted } from 'vue'

const breakpoints = {
  sm: 640,
  md: 768,
  lg: 1024,
} as const

export type Breakpoint = keyof typeof breakpoints

/**
 * Reactive breakpoint detection composable.
 * Returns boolean refs for each breakpoint threshold.
 */
export function useBreakpoint() {
  const isSm = ref(false)
  const isMd = ref(false)
  const isLg = ref(false)

  const update = () => {
    const w = window.innerWidth
    isSm.value = w >= breakpoints.sm
    isMd.value = w >= breakpoints.md
    isLg.value = w >= breakpoints.lg
  }

  let mql: MediaQueryList | undefined

  onMounted(() => {
    update()
    // Use matchMedia for efficient change detection at the md threshold
    mql = window.matchMedia(`(min-width: ${breakpoints.md}px)`)
    mql.addEventListener('change', update)
    // Also listen for resize to keep sm/lg accurate
    window.addEventListener('resize', update)
  })

  onUnmounted(() => {
    mql?.removeEventListener('change', update)
    window.removeEventListener('resize', update)
  })

  return { isSm, isMd, isLg, breakpoints }
}
