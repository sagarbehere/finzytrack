import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { NodeGlobalsPolyfillPlugin } from '@esbuild-plugins/node-globals-polyfill'
import { NodeModulesPolyfillPlugin } from '@esbuild-plugins/node-modules-polyfill'
import monacoEditorPlugin from 'vite-plugin-monaco-editor'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    monacoEditorPlugin.default({})
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // Polyfill Node.js modules for browser compatibility
      stream: 'stream-browserify',
      timers: 'timers-browserify',
      buffer: 'buffer',
      util: 'util',
    },
  },
  define: {
    global: 'globalThis',
  },
  optimizeDeps: {
    include: ['ofx-js'],
    esbuildOptions: {
      // Node.js global to browser globalThis
      define: {
        global: 'globalThis',
      },
      // Enable esbuild polyfill plugins
      plugins: [
        NodeGlobalsPolyfillPlugin({
          buffer: true,
        }),
        NodeModulesPolyfillPlugin(),
      ],
    },
  },
  server: {
    port: 3000,
    host: '127.0.0.1',
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['vue', 'vue-router'],
          ui: ['@headlessui/vue', '@heroicons/vue'],
          parsing: ['ofx-js'],
        },
      },
    },
  },
})
