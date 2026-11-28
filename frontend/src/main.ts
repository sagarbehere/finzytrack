import { createApp, defineAsyncComponent } from 'vue'

import './app/theme.css'

import App from './App.vue'
import { createApi, installApi } from './api/client'
import { createErrorSink, installErrors } from './app/errors'
import { createAppQueryClient, DESKTOP_CACHE_BOUNDS, installQueryClient } from './app/queryClient'

// The app's foundations (S0166): one error sink, one API layer, one bounded QueryClient. The backend's origin is the
// page's own for now; the shell injects its port with the first shell slice (ARCHITECTURE.md §5).
// S0170: the design gallery is a dev-server page (`pnpm dev`, then /dev/gallery). A production build makes
// `import.meta.env.DEV` false, so this branch and the gallery's chunk are dropped (tests/unit/app/galleryBuild.test.ts).
const gallery = import.meta.env.DEV && window.location.pathname === '/dev/gallery'
const app = createApp(gallery ? defineAsyncComponent(() => import('./app/gallery/GalleryPage.vue')) : App)
const errors = createErrorSink()
installErrors(app, errors)
installApi(app, createApi({ baseUrl: window.location.origin }))
installQueryClient(app, createAppQueryClient(DESKTOP_CACHE_BOUNDS, errors))
app.mount('#app')
