import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { vFormError } from './directives/formError.js'
import { OpenAPI } from './services/generated-api'
import { configureNLParser } from './services/nlParser'
import { configureSQLAssistant } from './services/sqlAssistant'

// Configure API base URL based on environment
OpenAPI.BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

// Configure NL transaction parser (set VITE_LLM_API_URL to enable LLM parsing)
if (import.meta.env.VITE_LLM_API_URL) {
  const llmConfig = {
    apiUrl: import.meta.env.VITE_LLM_API_URL,
    model: import.meta.env.VITE_LLM_MODEL || 'gpt-oss-20b',
    apiKey: import.meta.env.VITE_LLM_API_KEY || '',
  }
  configureNLParser(llmConfig)
  configureSQLAssistant(llmConfig)
}

const app = createApp(App)

app.use(router)
app.directive('form-error', vFormError)

app.mount('#app')
