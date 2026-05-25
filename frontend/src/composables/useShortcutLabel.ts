const isMac = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '')

export function useShortcutLabel() {
  return {
    isMac,
    modKey: isMac ? '⌘' : 'Ctrl',
    modEnter: isMac ? '⌘↵' : 'Ctrl+↵',
  }
}
