// The one QueryClient (L2-03; ADR-0037; ARCHITECTURE.md §3.2, §6.4) declares its bound (INV-10: gcTime and a cap
// on cached queries, with eviction), keeps ledger-derived data until an event invalidates it (staleTime: Infinity),
// leaves Retry to the user (no automatic retry), and sends every query or mutation error to the one error path once,
// unless a query asks for its error as state (`meta.errors: 'state'`). A cancelled query is never shown.
import { CancelledError, QueryObserver } from '@tanstack/vue-query'
import { describe, expect, it } from 'vitest'

import { clientError } from '../../../src/api/errors'
import { createErrorSink } from '../../../src/app/errors'
import { createAppQueryClient, DESKTOP_CACHE_BOUNDS } from '../../../src/app/queryClient'

const bounds = { gcTimeMs: 60_000, maxQueries: 3 }

describe('[L2-03][INV-10] the QueryClient declares its bound', () => {
  it('has a finite gcTime, staleTime Infinity and no automatic retry', () => {
    const client = createAppQueryClient(bounds, createErrorSink())
    const q = client.getDefaultOptions().queries
    expect(q?.gcTime).toBe(60_000)
    expect(q?.staleTime).toBe(Infinity)
    expect(q?.retry).toBe(false)
    expect(client.getDefaultOptions().mutations?.retry).toBe(false)
  })

  it('has desktop defaults that are finite', () => {
    expect(Number.isSafeInteger(DESKTOP_CACHE_BOUNDS.gcTimeMs)).toBe(true)
    expect(Number.isSafeInteger(DESKTOP_CACHE_BOUNDS.maxQueries)).toBe(true)
    expect(DESKTOP_CACHE_BOUNDS.maxQueries).toBeGreaterThan(0)
  })

  // must-not-pass: a client without a bound is refused
  it.each([
    ['an infinite gcTime', { gcTimeMs: Infinity, maxQueries: 3 }],
    ['no cap', { gcTimeMs: 60_000, maxQueries: Infinity }],
    ['a zero cap', { gcTimeMs: 60_000, maxQueries: 0 }],
    ['a negative gcTime', { gcTimeMs: -1, maxQueries: 3 }],
    ['a fractional cap', { gcTimeMs: 60_000, maxQueries: 2.5 }],
  ])('refuses %s', (_label, b) => {
    expect(() => createAppQueryClient(b, createErrorSink())).toThrow()
  })

  it('evicts the oldest unobserved queries above the cap', () => {
    const client = createAppQueryClient(bounds, createErrorSink())
    for (const n of [1, 2, 3, 4, 5]) client.setQueryData(['app', `/q${String(n)}`, null], n)
    const keys = client
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey[1])
    expect(keys).toEqual(['/q3', '/q4', '/q5'])
  })

  it('never evicts an observed query', () => {
    const client = createAppQueryClient(bounds, createErrorSink())
    client.setQueryData(['app', '/watched', null], 0)
    const observer = new QueryObserver(client, { queryKey: ['app', '/watched', null], queryFn: () => 0 })
    const unsubscribe = observer.subscribe(() => undefined)
    for (const n of [1, 2, 3, 4, 5]) client.setQueryData(['app', `/q${String(n)}`, null], n)
    const keys = client
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey[1])
    expect(keys).toContain('/watched')
    expect(keys).toHaveLength(3)
    unsubscribe()
  })
  // S0166 verifier F5: a query made by an observer (useQuery's path) is added before its observer subscribes, so
  // with the cap full of observed queries it is the only one without an observer, and must not be the one dropped.
  it('keeps a query just added by an observer when the cap is full of observed queries', () => {
    const client = createAppQueryClient(bounds, createErrorSink())
    const watch = (name: string) =>
      new QueryObserver(client, { queryKey: ['app', name, null], queryFn: () => 0, enabled: false }).subscribe(
        () => undefined,
      )
    const unsubscribe = ['/w1', '/w2', '/w3', '/new'].map(watch)
    const keys = client
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey[1])
    expect(keys).toEqual(['/w1', '/w2', '/w3', '/new'])
    unsubscribe.forEach((u) => {
      u()
    })
  })
})

describe('[L2-05] query and mutation errors reach the one error path once', () => {
  it('records a failing query once', async () => {
    const sink = createErrorSink()
    const client = createAppQueryClient(bounds, sink)
    const error = clientError('CLIENT_NETWORK')
    await expect(
      client.query({
        queryKey: ['app', '/fails', null],
        queryFn: () => Promise.reject(error),
      }),
    ).rejects.toBe(error)
    // a caller that also presents what it caught adds nothing
    sink.present(error)
    expect(sink.notices.value.map((n) => n.error)).toEqual([error])
  })

  it('records nothing for a query that asks for its error as state', async () => {
    const sink = createErrorSink()
    const client = createAppQueryClient(bounds, sink)
    await expect(
      client.query({
        queryKey: ['app', '/state', null],
        queryFn: () => Promise.reject(clientError('CLIENT_NETWORK')),
        meta: { errors: 'state' },
      }),
    ).rejects.toBeDefined()
    expect(sink.notices.value).toEqual([])
  })

  it('records nothing for a cancelled query', async () => {
    const sink = createErrorSink()
    const client = createAppQueryClient(bounds, sink)
    await expect(
      client.query({ queryKey: ['app', '/cancelled', null], queryFn: () => Promise.reject(new CancelledError()) }),
    ).rejects.toBeDefined()
    await expect(
      client.query({
        queryKey: ['app', '/aborted', null],
        queryFn: () => Promise.reject(clientError('CLIENT_ABORTED')),
      }),
    ).rejects.toBeDefined()
    expect(sink.notices.value).toEqual([])
  })

  it('records a failing mutation once, unless it asks for its error as state', async () => {
    const sink = createErrorSink()
    const client = createAppQueryClient(bounds, sink)
    const error = clientError('CLIENT_NETWORK')
    const run = (meta?: { errors: 'state' }) =>
      client
        .getMutationCache()
        .build(client, { mutationFn: () => Promise.reject(error), ...(meta ? { meta } : {}) })
        .execute(undefined)
    await expect(run()).rejects.toBe(error)
    await expect(run({ errors: 'state' })).rejects.toBe(error)
    expect(sink.notices.value.map((n) => n.error)).toEqual([error])
  })
})
