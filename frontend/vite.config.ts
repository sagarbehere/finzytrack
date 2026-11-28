import { readFileSync } from 'node:fs'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// BL-0032 (ADR-0040): the desktop window's CSP is also the preview server's header, so the acceptance harness loads
// the page under the shipped policy. The dev server sends none: hot reload injects inline styles and opens a
// WebSocket, and dev is not shipped.
const tauri = JSON.parse(readFileSync(new URL('../shell/src-tauri/tauri.conf.json', import.meta.url), 'utf8')) as {
  app: { security: { csp: string } }
}

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  build: { target: 'safari17' },
  preview: { headers: { 'Content-Security-Policy': tauri.app.security.csp } },
  test: {
    environment: 'happy-dom',
    include: ['tests/unit/**/*.test.ts'],
  },
})
