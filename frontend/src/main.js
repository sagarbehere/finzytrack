import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { vFormError } from './directives/formError.js'
import { OpenAPI } from './services/generated-api'
import { ConfigService } from './services/generated-api'
import { configureNLParser } from './services/nlParser'
import { configureSQLAssistant } from './services/sqlAssistant'

async function initApp() {
  OpenAPI.BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

  try {
    const response = await ConfigService.getConfigEndpointApiConfigGet()
    const llm = response.data?.ai?.llm
    if (llm?.api_url) {
      const llmConfig = {
        apiUrl: llm.api_url,
        apiKey: llm.api_key || '',
        model: llm.model || '',
        temperature: llm.temperature,
        maxTokens: llm.max_tokens,
      }
      configureNLParser(llmConfig)
      configureSQLAssistant(llmConfig)
    }
  } catch (e) {
    console.warn('Could not load LLM config from backend; LLM features disabled.', e)
  }

  const app = createApp(App)
  app.use(router)
  app.directive('form-error', vFormError)
  app.mount('#app')
}

initApp()
