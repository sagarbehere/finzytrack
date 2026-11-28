// @vitest-environment node
// One CSP for every place the page is served today (BL-0032, ADR-0040, INV-09): the desktop window's policy in
// tauri.conf.json (checked by scripts/tests/test_shell_config.py) is also the HTTP header of `vite preview`, which the
// acceptance harness loads, so a page that breaks the policy fails the acceptance run. The dev server sends none
// (hot reload injects inline styles and opens a WebSocket); it is not shipped.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import config from '../../../vite.config'

const tauri = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../../../shell/src-tauri/tauri.conf.json', import.meta.url)), 'utf8'),
) as { app: { security: { csp: string } } }

function header(headers: unknown, name: string): unknown {
  if (typeof headers !== 'object' || headers === null) return undefined
  const entry = Object.entries(headers).find(([k]) => k.toLowerCase() === name.toLowerCase())
  return entry?.[1]
}

describe('[BL-0032][INV-09] the web page is served under the desktop CSP', () => {
  it('sends the desktop CSP as the preview server header', () => {
    expect(tauri.app.security.csp).toMatch(/script-src 'self' 'sha256-/)
    expect(header(config.preview?.headers, 'Content-Security-Policy')).toBe(tauri.app.security.csp)
  })

  it('sends no report-only header that could stand in for it', () => {
    expect(header(config.preview?.headers, 'Content-Security-Policy-Report-Only')).toBeUndefined()
  })
})
