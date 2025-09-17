import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { vFormError } from './directives/formError.js'
import { OpenAPI } from './services/generated-api'

// Configure API base URL based on environment
OpenAPI.BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

const app = createApp(App)

app.use(router)
app.directive('form-error', vFormError)

app.mount('#app')
