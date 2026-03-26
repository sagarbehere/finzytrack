/**
 * Helper for patching specific config fields via the GUI settings editor.
 *
 * Accepts a plain object containing only the fields to change. Nested fields
 * are supported as nested objects, e.g.:
 *   patchConfig({ ai: { llm: { api_url: '...' } } })
 */

import { ConfigService } from '@/services/generated-api'
import type { Config } from '@/services/generated-api'

export interface ConfigPatchResult {
  config: Config
  restart_required: boolean
  restart_reason: string | null
  notice: string | null
}

export async function patchConfig(patch: Record<string, unknown>): Promise<ConfigPatchResult> {
  try {
    const response = await ConfigService.patchConfigEndpointApiConfigPatch(patch)

    if (!response.success || !response.data) {
      throw new Error(response.error?.message ?? 'Failed to save settings')
    }

    return {
      config: response.data.config,
      restart_required: response.data.restart_required,
      restart_reason: response.data.restart_reason ?? null,
      notice: response.data.notice ?? null,
    }
  } catch (e: any) {
    const detail = e.body?.error?.details?.errors?.join('\n')
    const message = e.body?.error?.message ?? e.message ?? 'Failed to save settings'
    throw new Error(detail ?? message)
  }
}
