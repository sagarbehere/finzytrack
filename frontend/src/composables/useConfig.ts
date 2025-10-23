/**
 * Composable for managing global application configuration.
 *
 * Provides a centralized, reactive config cache that is:
 * - Loaded once on app startup
 * - Updated when user saves config via file editor
 * - Accessible from any component without prop drilling
 * - Fully type-safe with auto-generated types
 */

import { ref, readonly } from 'vue'
import { ConfigService } from '@/services/generated-api'
import type { Config } from '@/services/generated-api'

// Global config cache (shared across all component instances)
const cachedConfig = ref<Config | null>(null)
const isLoading = ref(false)
const loadError = ref<string | null>(null)

export function useConfig() {
  /**
   * Load configuration from backend.
   *
   * Only loads if not already cached. Safe to call multiple times.
   */
  const loadConfig = async (): Promise<void> => {
    // Skip if already loaded or currently loading
    if (cachedConfig.value || isLoading.value) return

    isLoading.value = true
    loadError.value = null

    try {
      const response = await ConfigService.getConfigEndpointApiConfigGet()

      if (response.success && response.data) {
        cachedConfig.value = response.data
      } else {
        loadError.value = response.error?.message || 'Failed to load configuration'
      }
    } catch (error: any) {
      loadError.value = error?.message || 'Failed to load configuration'
      console.error('Failed to load config:', error)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Update cached configuration.
   *
   * Called by FileEditor after successful config file save to keep
   * the global cache in sync with backend state.
   *
   * @param newConfig - Updated configuration object from backend
   */
  const updateConfig = (newConfig: Config): void => {
    cachedConfig.value = newConfig
  }

  /**
   * Force reload configuration from backend.
   *
   * Bypasses cache and fetches fresh config. Use when you know
   * config has changed externally (e.g., manual file edit).
   */
  const reloadConfig = async (): Promise<void> => {
    cachedConfig.value = null // Clear cache
    await loadConfig()
  }

  /**
   * Get current config synchronously.
   *
   * Returns null if not loaded yet. Check isLoading.value first.
   */
  const getConfig = (): Config | null => {
    return cachedConfig.value
  }

  return {
    // Read-only reactive state
    config: readonly(cachedConfig),
    isLoading: readonly(isLoading),
    loadError: readonly(loadError),

    // Actions
    loadConfig,
    updateConfig,
    reloadConfig,
    getConfig,
  }
}
