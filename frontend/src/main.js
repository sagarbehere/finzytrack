import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { vFormError } from './directives/formError.js'
import { OpenAPI } from './services/generated-api'
import { ConfigService } from './services/generated-api'
import { OpenAPI as EmailOpenAPI } from './services/generated-email-api'
import { useConfig } from './composables/useConfig'
async function initApp() {
  // Empty string = relative URLs, works when frontend is served by the backend (packaged app).
  // In development, VITE_API_BASE_URL can be set, or the Vite dev proxy routes /api to the backend.
  OpenAPI.BASE = import.meta.env.VITE_API_BASE_URL || ''

  try {
    const response = await ConfigService.getConfigEndpointApiConfigGet()

    if (response.data) {
      // Seed the global config cache so all composables (e.g. useEmailImporter)
      // see the correct config immediately on first mount, without a second fetch.
      useConfig().updateConfig(response.data)
    }

    // Also set EmailOpenAPI.BASE directly as a fallback for the initial render,
    // before Vue's reactivity has had a chance to run watchEffect in useEmailImporter.
    EmailOpenAPI.BASE = response.data?.email_service?.base_url || ''
  } catch (e) {
    console.warn('Could not load initial config from backend.', e)
  }

  const app = createApp(App)
  app.use(router)
  app.directive('form-error', vFormError)
  app.mount('#app')
}

initApp()
