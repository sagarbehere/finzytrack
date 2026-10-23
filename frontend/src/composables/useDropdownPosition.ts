import { ref, onBeforeUnmount, type Ref, type CSSProperties } from 'vue'

/**
 * Tracks the position of a trigger element and returns fixed-position styles
 * for a teleported dropdown. Callers invoke `updatePosition` when the dropdown
 * opens (focus/click); from then until focus leaves the trigger it also
 * repositions on scroll and resize. Listening only while in use matters: a
 * table can mount hundreds of dropdowns, and each one listens on every
 * ancestor scroll container.
 */
export function useDropdownPosition(triggerRef: Ref<HTMLElement | null>) {
  const dropdownStyle = ref<CSSProperties>({})

  const reposition = () => {
    if (!triggerRef.value) return
    const rect = triggerRef.value.getBoundingClientRect()
    dropdownStyle.value = {
      top: `${rect.bottom + 4}px`,
      left: `${rect.left}px`,
      minWidth: `${rect.width}px`,
    }
  }

  let cleanups: (() => void)[] = []

  const stopTracking = () => {
    cleanups.forEach(fn => fn())
    cleanups = []
  }

  const onFocusOut = (e: FocusEvent) => {
    const trigger = triggerRef.value
    if (trigger && e.relatedTarget instanceof Node && trigger.contains(e.relatedTarget)) return
    stopTracking()
  }

  const startTracking = () => {
    const trigger = triggerRef.value
    if (cleanups.length > 0 || !trigger) return
    // Listen on all ancestor scroll containers
    let el: HTMLElement | null = trigger
    while (el) {
      el.addEventListener('scroll', reposition, { passive: true })
      const captured = el
      cleanups.push(() => captured.removeEventListener('scroll', reposition))
      el = el.parentElement
    }
    window.addEventListener('scroll', reposition, { passive: true })
    window.addEventListener('resize', reposition, { passive: true })
    trigger.addEventListener('focusout', onFocusOut)
    cleanups.push(() => window.removeEventListener('scroll', reposition))
    cleanups.push(() => window.removeEventListener('resize', reposition))
    cleanups.push(() => trigger.removeEventListener('focusout', onFocusOut))
  }

  const updatePosition = () => {
    reposition()
    startTracking()
  }

  onBeforeUnmount(() => {
    stopTracking()
  })

  return { dropdownStyle, updatePosition }
}
