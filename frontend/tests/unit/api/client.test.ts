// @vitest-environment node
// The one API access layer (L2-06; ARCHITECTURE.md §3.1, §4.1; ADR-0038): a call unwraps the envelope, turns every
// failure into one AppError (L2-05), passes the caller's AbortSignal (ADR-0037: a switch aborts its queries), and a
// workspace call is accepted only from the workspace its path named (INV-28's `meta.workspace_id` client assertion,
// BL-0028, BL-0107). The fetch is a stub: no server runs.
import { describe, expect, it } from 'vitest'
import { createApp } from 'vue'

import { createApi, installApi, readQuery, useApi } from '../../../src/api/client'
import { AppError } from '../../../src/api/errors'
import { queryKey } from '../../../src/api/keys'

const base = 'http://127.0.0.1:9'
const meta = (workspace_id: string | null) => ({
  request_id: 'req-1',
  workspace_id,
  ledger_version: null,
  config_version: null,
})
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function stub(answer: (request: Request) => Response | Promise<Response>) {
  const seen: Request[] = []
  const fetch = async (input: Request) => {
    seen.push(input)
    return answer(input)
  }
  return { fetch: fetch as unknown as typeof globalThis.fetch, seen }
}

// Only /health exists in the contract today; a workspace path is reached through the untyped call.
type LooseGet = (path: string, params: unknown, signal?: AbortSignal) => Promise<{ data: unknown; meta: unknown }>
const looseGet =
  (api: ReturnType<typeof createApi>): LooseGet =>
  (path, params, signal) =>
    api.get(path as '/health', params as Record<string, never>, signal)

async function caught(p: Promise<unknown>): Promise<AppError> {
  const e: unknown = await p.then(
    () => undefined,
    (thrown: unknown) => thrown,
  )
  expect(e).toBeInstanceOf(AppError)
  return e as AppError
}

describe('[L2-06] a call through the API layer', () => {
  it('unwraps data and returns meta', async () => {
    const s = stub(() => json(200, { data: { status: 'ok', version: '0.4.0' }, meta: meta(null) }))
    const answer = await createApi({ baseUrl: base, fetch: s.fetch }).get('/health', {})
    expect(answer).toEqual({ data: { status: 'ok', version: '0.4.0' }, meta: meta(null) })
    expect(s.seen[0]?.url).toBe(`${base}/health`)
    expect(s.seen[0]?.method).toBe('GET')
  })

  it('throws the decoded AppError of an error answer', async () => {
    const body = {
      error: {
        code: 'ROUTE_NOT_FOUND',
        class: 'not_found',
        message: 'This address does not exist in this version of Finzytrack.',
        data_safe: true,
        next_step: 'Reload the window.',
        issues: [],
        retryable: false,
        retry_after_ms: null,
        details: null,
        correlation_id: 'req-1',
      },
      meta: meta(null),
    }
    const s = stub(() => json(404, body))
    const e = await caught(createApi({ baseUrl: base, fetch: s.fetch }).get('/health', {}))
    expect(e.code).toBe('ROUTE_NOT_FOUND')
    expect(e.message).toBe('This address does not exist in this version of Finzytrack.')
    expect(e.status).toBe(404)
  })

  it.each([
    ['an HTML error page', () => new Response('<html>Bad gateway</html>', { status: 502 })],
    ['a 2xx that is not JSON', () => new Response('ok', { status: 200 })],
    ['a 2xx without the envelope', () => json(200, { status: 'ok' })],
  ])('throws CLIENT_DECODE for %s', async (_label, answer) => {
    const e = await caught(createApi({ baseUrl: base, fetch: stub(answer).fetch }).get('/health', {}))
    expect(e.code).toBe('CLIENT_DECODE')
  })

  it('throws CLIENT_NETWORK when no answer came', async () => {
    const s = stub(() => Promise.reject(new TypeError('Load failed')))
    expect((await caught(createApi({ baseUrl: base, fetch: s.fetch }).get('/health', {}))).code).toBe('CLIENT_NETWORK')
  })

  it("passes the caller's signal, and an aborted call is CLIENT_ABORTED", async () => {
    const controller = new AbortController()
    const s = stub(
      (request) =>
        new Promise<Response>((_resolve, reject) => {
          request.signal.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'))
          })
        }),
    )
    const call = createApi({ baseUrl: base, fetch: s.fetch }).get('/health', {}, controller.signal)
    controller.abort()
    expect((await caught(call)).code).toBe('CLIENT_ABORTED')
    expect(s.seen[0]?.signal.aborted).toBe(true)
  })
})

describe('[INV-28] a workspace call is accepted only from the workspace it named', () => {
  const path = '/api/w/{ws}/accounts'

  it('accepts the answer of the named workspace', async () => {
    const s = stub(() => json(200, { data: [], meta: meta('ws-a') }))
    const get = looseGet(createApi({ baseUrl: base, fetch: s.fetch }))
    expect(await get(path, { path: { ws: 'ws-a' } })).toEqual({ data: [], meta: meta('ws-a') })
    expect(s.seen[0]?.url).toBe(`${base}/api/w/ws-a/accounts`)
  })

  // must-not-pass
  it.each([
    ['another workspace', 'ws-b'],
    ['no workspace', null],
  ])('refuses the answer of %s as CLIENT_WRONG_WORKSPACE', async (_label, served) => {
    const s = stub(() => json(200, { data: [], meta: meta(served) }))
    const get = looseGet(createApi({ baseUrl: base, fetch: s.fetch }))
    const e = await caught(get(path, { path: { ws: 'ws-a' } }))
    expect(e.code).toBe('CLIENT_WRONG_WORKSPACE')
  })
})

describe('[L2-03] a read query', () => {
  it('uses the same parameters for its key and its request, and consumes the signal', async () => {
    const s = stub(() => json(200, { data: { status: 'ok', version: '0.4.0' }, meta: meta(null) }))
    const q = readQuery(createApi({ baseUrl: base, fetch: s.fetch }), '/health', {})
    expect(q.queryKey).toEqual(queryKey('/health', {}))
    const controller = new AbortController()
    expect(await q.queryFn({ signal: controller.signal })).toEqual({ status: 'ok', version: '0.4.0' })
    // S0166 verifier F3: the request follows the query's own signal (Vue Query aborts it on a switch)
    expect(s.seen[0]?.signal.aborted).toBe(false)
    controller.abort()
    expect(s.seen[0]?.signal.aborted).toBe(true)
  })
})

const Root = {}

describe('[L2-06] one API per app', () => {
  it('is what the code of that app reads', () => {
    const app = createApp(Root)
    const api = createApi({ baseUrl: base })
    installApi(app, api)
    expect(app.runWithContext(() => useApi())).toBe(api)
  })

  it('is refused when none is installed', () => {
    expect(() => createApp(Root).runWithContext(() => useApi())).toThrow('no API installed')
  })
})
