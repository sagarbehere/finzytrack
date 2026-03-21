/**
 * Composable for patching specific config fields via the GUI settings editor.
 *
 * Accepts a plain object containing only the fields to change. Nested fields
 * are supported as nested objects, e.g.:
 *   patchConfig({ ai: { llm: { api_url: '...' } } })
 */

import { OpenAPI } from '@/services/generated-api'
import type { Config } from '@/services/generated-api'

export interface ConfigPatchResult {
  config: Config
  restart_required: boolean
  restart_reason: string | null
}

export async function patchConfig(patch: Record<string, unknown>): Promise<ConfigPatchResult> {
  const response = await fetch(`${OpenAPI.BASE}/api/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })

  const envelope = await response.json()

  if (!envelope.success) {
    const detail = envelope.error?.details?.errors?.join('\n')
    const message = envelope.error?.message ?? 'Failed to save settings'
    throw new Error(detail ?? message)
  }

  return envelope.data as ConfigPatchResult
}
