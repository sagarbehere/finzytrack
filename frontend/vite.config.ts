import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  build: { target: 'safari17' },
  test: {
    environment: 'happy-dom',
    include: ['tests/unit/**/*.test.ts'],
  },
})
