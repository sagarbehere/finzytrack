// Query keys (ADR-0037; ARCHITECTURE.md §3.2; L2-03, S0166). A key is built from the operation's declared scope
// (`x-ft-scope`, generated into keys.gen.ts from the contract), never by hand:
//   app scope:           ['app', path, query]
//   ledger/config scope: ['ws', ws, scope, path, pathParams, query]
// `ws` is the same `{ws}` path parameter the request sends (ADR-0018: no global "current workspace"), so two
// workspaces never share a key below ['ws'] and a switch never shows another workspace's data (INV-28); and every
// key of one workspace and scope shares the prefix ['ws', ws, scope], which the event invalidation will use.
import type { paths } from './generated/openapi'
import { operations } from './generated/keys.gen'

type Scope = 'app' | 'ledger' | 'config'
type OperationTable = Readonly<Record<string, Readonly<Partial<Record<string, { scope: Scope }>>>>>

interface KeyParams {
  path?: Readonly<Record<string, unknown>>
  query?: Readonly<Record<string, unknown>>
}

/** A path of the contract that answers GET. */
export type ReadPath = {
  [P in keyof paths]: paths[P] extends { get: { parameters: unknown } } ? P : never
}[keyof paths]
export type ReadParams<P extends ReadPath> = paths[P] extends { get: { parameters: infer A } } ? A : never

export type QueryKey = readonly unknown[]

/** The key of a GET of `path` with `params`, by the scope `table` declares. Fails closed on an undeclared path. */
export function keyFor(table: OperationTable, path: string, params: KeyParams): QueryKey {
  const scope = Object.hasOwn(table, path) ? table[path]?.['get']?.scope : undefined
  if (scope === undefined) throw new Error(`no declared scope for GET ${path}`)
  const query = params.query ?? null
  if (scope === 'app') return ['app', path, query]
  const { ws, ...rest } = params.path ?? {}
  if (typeof ws !== 'string' || ws === '') throw new Error(`GET ${path} is workspace-scoped and needs its ws`)
  return ['ws', ws, scope, path, rest, query]
}

/** The query key of a GET of the contract. */
export function queryKey<P extends ReadPath>(path: P, params: ReadParams<P>): QueryKey {
  return keyFor(operations, path, params as KeyParams)
}
