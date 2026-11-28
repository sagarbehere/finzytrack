// The one API access layer (L2-06; ARCHITECTURE.md §3.1, §4.1; ADR-0038; S0166): every call goes through here, over
// openapi-fetch and the generated types. A call returns the envelope's `{data, meta}`, or throws one AppError
// (src/api/errors.ts); it passes the caller's AbortSignal (ADR-0037); and a workspace call is accepted only from the
// workspace its path named, so an answer from another workspace never reaches a screen (INV-28's client assertion).
// One instance per app (`createApi`, provided by `installApi`), never a module-level client (EP-04).
// Not yet here: the typed SSE reader (`sse.ts`, with the first stream) and writes (with the first write route).
import createClient from 'openapi-fetch'
import { inject, type App, type InjectionKey } from 'vue'

import { clientError, decodeError, decodeSuccess, failureOf, type Meta } from './errors'
import type { paths } from './generated/openapi'
import { queryKey, type QueryKey, type ReadParams, type ReadPath } from './keys'

export interface ApiOptions {
  /** The backend's origin, e.g. `http://127.0.0.1:52345`. */
  baseUrl: string
  /** For tests; the platform's fetch otherwise. */
  fetch?: typeof globalThis.fetch
}

type ReadData<P extends ReadPath> = paths[P] extends {
  get: { responses: { 200: { content: { 'application/json': { data: infer D } } } } }
}
  ? D
  : never

interface Answer<T> {
  data: T
  meta: Meta
}

export interface Api {
  get<P extends ReadPath>(path: P, params: ReadParams<P>, signal?: AbortSignal): Promise<Answer<ReadData<P>>>
}

interface RawResult {
  data?: unknown
  error?: unknown
  response: Response
}
type RawGet = (path: string, init: { params: unknown; signal?: AbortSignal }) => Promise<RawResult>

const WORKSPACE_PREFIX = '/api/w/{ws}/'

function namedWorkspace(params: unknown): unknown {
  if (typeof params !== 'object' || params === null) return undefined
  const path: unknown = (params as { path?: unknown }).path
  return typeof path === 'object' && path !== null ? (path as { ws?: unknown }).ws : undefined
}

export function createApi(options: ApiOptions): Api {
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
  })
  // The generic GET is typed for one path at a time; this layer checks every answer itself, so it calls it untyped.
  const rawGet = client.GET.bind(client) as unknown as RawGet

  async function get(path: string, params: unknown, signal?: AbortSignal): Promise<Answer<unknown>> {
    let result: RawResult
    try {
      result = await rawGet(path, { params, ...(signal === undefined ? {} : { signal }) })
    } catch (thrown) {
      throw failureOf(thrown, signal)
    }
    const { status, ok } = result.response
    if (!ok) throw decodeError(status, result.error)
    const answer = decodeSuccess(status, result.data)
    if (path.startsWith(WORKSPACE_PREFIX)) {
      const ws = namedWorkspace(params)
      if (typeof ws !== 'string' || answer.meta.workspace_id !== ws) throw clientError('CLIENT_WRONG_WORKSPACE', status)
    }
    return answer
  }

  return { get: get as Api['get'] }
}

/** A read as Vue Query options: its key and its request take the same parameters, and the request the signal. */
export function readQuery<P extends ReadPath>(
  api: Api,
  path: P,
  params: ReadParams<P>,
): { queryKey: QueryKey; queryFn: (context: { signal: AbortSignal }) => Promise<ReadData<P>> } {
  return {
    queryKey: queryKey(path, params),
    queryFn: async ({ signal }) => (await api.get(path, params, signal)).data,
  }
}

const apiKey: InjectionKey<Api> = Symbol('ft-api')

export function installApi(app: App, api: Api): void {
  app.provide(apiKey, api)
}

/** The app's API, inside a component or composable of an app that installed one. */
export function useApi(): Api {
  const api = inject(apiKey)
  if (api === undefined) throw new Error('useApi: no API installed')
  return api
}
