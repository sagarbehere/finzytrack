// The one QueryClient (L2-03; ADR-0037; ARCHITECTURE.md §3.2, §6.4; S0166). It declares its bound (INV-10): queries
// nobody watches are dropped after `gcTimeMs`, and above `maxQueries` the oldest unobserved ones go first; an
// observed query (on screen) is never dropped. The bound is a setting with desktop defaults, injected by main.ts
// (read from the deployment's configuration later, like the backend's, BL-0229). The lint refuses a QueryClient made
// elsewhere and a `gcTime: Infinity` anywhere (eslint.config.js, tests/unit/gates.test.ts).
// Ledger-derived data stays fresh until an event invalidates it (`staleTime: Infinity`; the event invalidation is
// BL-0323), and Retry is the user's (§6.4), so nothing retries by itself. Every query or mutation error goes to the
// one error path (src/app/errors), except a cancelled one and one whose query asks for its error as state.
// Stated limit: eviction can remove an unobserved query while `fetchQuery`/`ensureQueryData` is fetching it, which
// cancels that fetch (the caller gets a CancelledError); a screen reads through `useQuery`, whose query is observed.
import {
  CancelledError,
  MutationCache,
  QueryCache,
  QueryClient,
  VueQueryPlugin,
  type QueryCacheNotifyEvent,
} from '@tanstack/vue-query'
import type { App } from 'vue'

import type { ErrorSink } from './errors'

declare module '@tanstack/vue-query' {
  interface Register {
    queryMeta: { errors?: 'state' }
    mutationMeta: { errors?: 'state' }
  }
}

export interface CacheBounds {
  /** How long a query nobody watches is kept, in milliseconds. */
  gcTimeMs: number
  /** The most queries kept; above it the oldest unobserved ones are dropped. */
  maxQueries: number
}

export const DESKTOP_CACHE_BOUNDS: CacheBounds = { gcTimeMs: 5 * 60_000, maxQueries: 500 }

export function createAppQueryClient(bounds: CacheBounds, sink: ErrorSink): QueryClient {
  const { gcTimeMs, maxQueries } = bounds
  if (!Number.isSafeInteger(gcTimeMs) || gcTimeMs < 0)
    throw new Error(`gcTimeMs must be a finite time, not ${String(gcTimeMs)}`)
  if (!Number.isSafeInteger(maxQueries) || maxQueries < 1)
    throw new Error(`maxQueries must be a positive whole number, not ${String(maxQueries)}`)

  const present = (error: unknown, meta: { errors?: 'state' } | undefined) => {
    if (error instanceof CancelledError || meta?.errors === 'state') return
    sink.present(error)
  }
  const queryCache = new QueryCache({
    onError: (error, query) => {
      present(error, query.meta)
    },
  })
  const mutationCache = new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      present(error, mutation.meta)
    },
  })

  // Above the cap, drop the oldest queries nobody watches (the cache keeps them in the order they were added),
  // never the one just added.
  queryCache.subscribe((event: QueryCacheNotifyEvent) => {
    if (event.type !== 'added') return
    const all = queryCache.getAll()
    let excess = all.length - maxQueries
    for (const query of all) {
      if (excess <= 0) break
      if (query === event.query || query.getObserversCount() > 0) continue
      queryCache.remove(query)
      excess--
    }
  })

  return new QueryClient({
    queryCache,
    mutationCache,
    defaultOptions: {
      queries: { gcTime: gcTimeMs, staleTime: Infinity, retry: false },
      mutations: { gcTime: gcTimeMs, retry: false },
    },
  })
}

export function installQueryClient(app: App, client: QueryClient): void {
  app.use(VueQueryPlugin, { queryClient: client })
}
