import { createApp } from 'vue'

import './app/theme.css'

import App from './App.vue'
import { createApi, installApi } from './api/client'
import { createErrorSink, installErrors } from './app/errors'
import { createAppQueryClient, DESKTOP_CACHE_BOUNDS, installQueryClient } from './app/queryClient'

// The app's foundations (S0166): one error sink, one API layer, one bounded QueryClient. The backend's origin is the
// page's own for now; the shell injects its port with the first shell slice (ARCHITECTURE.md §5).
const app = createApp(App)
const errors = createErrorSink()
installErrors(app, errors)
installApi(app, createApi({ baseUrl: window.location.origin }))
installQueryClient(app, createAppQueryClient(DESKTOP_CACHE_BOUNDS, errors))
app.mount('#app')
