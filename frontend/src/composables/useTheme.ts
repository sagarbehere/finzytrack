import { ref, computed, onMounted, watch } from 'vue'
import { getStorageAdapter, STORAGE_KEYS } from '@/services/storage'

// Theme options
export const THEMES = {
  SYSTEM: 'system',
  LIGHT: 'light',
  DARK: 'dark',
} as const

type ThemeValue = typeof THEMES[keyof typeof THEMES]

// Global theme state (singleton)
const currentTheme = ref<ThemeValue>(THEMES.SYSTEM)
const isDarkMode = ref(false)

export function useTheme() {
  // Check if user prefers dark mode
  const getSystemPreference = () => {
    if (typeof window !== 'undefined') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return false
  }

  // Apply theme to DOM
  const applyTheme = (theme: ThemeValue) => {
    if (typeof document === 'undefined') return

    const html = document.documentElement

    // Remove existing theme classes
    html.classList.remove('light', 'dark')

    // Apply new theme
    if (theme === THEMES.LIGHT) {
      html.classList.add('light')
      isDarkMode.value = false
    } else if (theme === THEMES.DARK) {
      html.classList.add('dark')
      isDarkMode.value = true
    } else {
      // System theme - check system preference
      const systemPrefersDark = getSystemPreference()
      if (systemPrefersDark) {
        html.classList.add('dark')
        isDarkMode.value = true
      } else {
        html.classList.add('light')
        isDarkMode.value = false
      }
    }
  }

  // Save theme via storage adapter
  const saveTheme = (theme: ThemeValue) => {
    getStorageAdapter().set(STORAGE_KEYS.THEME, theme)
  }

  // Load theme via storage adapter
  const loadTheme = (): ThemeValue => {
    const saved = getStorageAdapter().get<string>(STORAGE_KEYS.THEME)
    if (saved && (Object.values(THEMES) as string[]).includes(saved)) {
      return saved as ThemeValue
    }
    return THEMES.SYSTEM
  }

  // Set theme
  const setTheme = (theme: ThemeValue) => {
    if (!(Object.values(THEMES) as string[]).includes(theme)) {
      console.warn(`Invalid theme: ${theme}`)
      return
    }

    currentTheme.value = theme
    applyTheme(theme)
    saveTheme(theme)
  }

  // Toggle between themes (system -> light -> dark -> system)
  const toggleTheme = () => {
    const themes = [THEMES.SYSTEM, THEMES.LIGHT, THEMES.DARK]
    const currentIndex = themes.indexOf(currentTheme.value)
    const nextIndex = (currentIndex + 1) % themes.length
    setTheme(themes[nextIndex])
  }

  // Computed properties
  const themeIcon = computed(() => {
    if (currentTheme.value === THEMES.LIGHT) {
      return 'sun'
    } else if (currentTheme.value === THEMES.DARK) {
      return 'moon'
    } else {
      // System mode - show computer icon
      return 'computer'
    }
  })

  const themeLabel = computed(() => {
    switch (currentTheme.value) {
      case THEMES.LIGHT:
        return 'Light mode'
      case THEMES.DARK:
        return 'Dark mode'
      case THEMES.SYSTEM:
      default:
        return `System mode (${isDarkMode.value ? 'dark' : 'light'})`
    }
  })

  // Initialize theme on mount
  onMounted(() => {
    const savedTheme = loadTheme()
    setTheme(savedTheme)

    // Listen for system theme changes
    if (typeof window !== 'undefined') {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
      const handleChange = () => {
        if (currentTheme.value === THEMES.SYSTEM) {
          applyTheme(THEMES.SYSTEM)
        }
      }

      mediaQuery.addEventListener('change', handleChange)

      // Cleanup function
      return () => {
        mediaQuery.removeEventListener('change', handleChange)
      }
    }
  })

  // Watch for theme changes and apply them
  watch(currentTheme, (newTheme) => {
    applyTheme(newTheme)
  })

  return {
    currentTheme: computed(() => currentTheme.value),
    isDarkMode: computed(() => isDarkMode.value),
    themeIcon,
    themeLabel,
    setTheme,
    toggleTheme,
    THEMES,
  }
}
