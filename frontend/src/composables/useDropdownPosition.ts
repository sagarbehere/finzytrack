import { ref, onMounted, onBeforeUnmount, type Ref, type CSSProperties } from 'vue'

/**
 * Tracks the position of a trigger element and returns fixed-position styles
 * for a teleported dropdown. Repositions on scroll and resize.
 */
export function useDropdownPosition(triggerRef: Ref<HTMLElement | null>) {
  const dropdownStyle = ref<CSSProperties>({})

  const updatePosition = () => {
    if (!triggerRef.value) return
    const rect = triggerRef.value.getBoundingClientRect()
    dropdownStyle.value = {
      top: `${rect.bottom + 4}px`,
      left: `${rect.left}px`,
      minWidth: `${rect.width}px`,
    }
  }

  let cleanups: (() => void)[] = []

  const setupListeners = () => {
    teardownListeners()
    // Listen on all ancestor scroll containers
    let el: HTMLElement | null = triggerRef.value
    while (el) {
      el.addEventListener('scroll', updatePosition, { passive: true })
      const captured = el
      cleanups.push(() => captured.removeEventListener('scroll', updatePosition))
      el = el.parentElement
    }
    window.addEventListener('scroll', updatePosition, { passive: true })
    window.addEventListener('resize', updatePosition, { passive: true })
    cleanups.push(() => window.removeEventListener('scroll', updatePosition))
    cleanups.push(() => window.removeEventListener('resize', updatePosition))
  }

  const teardownListeners = () => {
    cleanups.forEach(fn => fn())
    cleanups = []
  }

  onMounted(() => {
    setupListeners()
  })

  onBeforeUnmount(() => {
    teardownListeners()
  })

  return { dropdownStyle, updatePosition }
}
