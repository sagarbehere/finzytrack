// @vitest-environment node
// The generated API types describe what the backend sends (ADR-0038; S0046). The types are generated offline from
// contracts/openapi.json by frontend/scripts/gen-api-types.mjs (openapi-typescript) and never hand-edited (INV-19):
// these checks fail in `vue-tsc` when the contract drifts from what the client expects. Spec: ARCHITECTURE.md §4.1
// (one envelope: `{data, meta: {request_id, workspace_id, ledger_version, config_version}}` or `{error, meta}`;
// every response field required, ADR-0038 amendment), §6.1 and §6.2 (the error codes and their typed details),
// ADR-0063 (`WORKSPACE_NOT_OPEN {open_workspace, switching}`), ADR-0018 (`WORKSPACE_NOT_READY {phase}`).
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, expectTypeOf, it } from 'vitest'

import type { components, paths } from '../../../src/api/generated/openapi'

type Schemas = components['schemas']
type ApiError = Schemas['ErrorEnvelope']['error']
type Meta = Schemas['Meta']

const generated = fileURLToPath(new URL('../../../src/api/generated/openapi.ts', import.meta.url))

describe('[S0046] the generated API types', () => {
  it('are generated, never hand-written', () => {
    const firstLine = readFileSync(generated, 'utf8').split('\n')[0] ?? ''
    expect(firstLine).toContain('GENERATED')
  })

  it('type the health answer in the envelope', () => {
    type Health = paths['/health']['get']['responses'][200]['content']['application/json']
    expectTypeOf<Health['data']>().toEqualTypeOf<{ status: 'ok'; version: string }>()
    expectTypeOf<Health['meta']>().toEqualTypeOf<Meta>()
  })

  it('make every meta field required, nullable where the server may have no value', () => {
    expectTypeOf<Meta>().toEqualTypeOf<{
      request_id: string
      workspace_id: string | null
      ledger_version: string | null
      config_version: string | null
    }>()
  })

  it('list every error code of the registry', () => {
    expectTypeOf<ApiError['code']>().toEqualTypeOf<
      | 'VALIDATION_FAILED'
      | 'ROUTE_NOT_FOUND'
      | 'WORKSPACE_UNKNOWN'
      | 'WORKSPACE_NOT_OPEN'
      | 'WORKSPACE_NOT_READY'
      | 'ENGINE_QUEUE_FULL'
      | 'JOB_CANCELLED'
      | 'ENGINE_STOPPED'
      | 'ENGINE_UNAVAILABLE'
      | 'INTERNAL'
    >()
  })

  it('type each code with its own details', () => {
    expectTypeOf<Extract<ApiError, { code: 'WORKSPACE_NOT_OPEN' }>['details']>().toEqualTypeOf<{
      open_workspace: string | null
      switching: boolean
    }>()
    expectTypeOf<Extract<ApiError, { code: 'WORKSPACE_NOT_READY' }>['details']>().toEqualTypeOf<{
      phase: 'WARMING'
    }>()
    // S0058: the engine-job codes (engine-jobs §11)
    expectTypeOf<Extract<ApiError, { code: 'JOB_CANCELLED' }>['details']>().toEqualTypeOf<{
      cannot_stop_ahead: boolean
    }>()
    expectTypeOf<Extract<ApiError, { code: 'ENGINE_STOPPED' }>['details']>().toEqualTypeOf<{
      reason: 'crashed' | 'killed_by_system'
    }>()
    expectTypeOf<Extract<ApiError, { code: 'ENGINE_UNAVAILABLE' }>['details']>().toEqualTypeOf<{
      reason: 'stopped_often' | 'cannot_start' | 'closing'
    }>()
    expectTypeOf<Extract<ApiError, { code: 'WORKSPACE_UNKNOWN' }>['details']>().toEqualTypeOf<null>()
  })

  it('give each code its class', () => {
    expectTypeOf<Extract<ApiError, { code: 'WORKSPACE_NOT_OPEN' }>['class']>().toEqualTypeOf<'conflict'>()
    expectTypeOf<Extract<ApiError, { code: 'INTERNAL' }>['class']>().toEqualTypeOf<'internal'>()
  })

  it('give a validation error its located issues', () => {
    expectTypeOf<Extract<ApiError, { code: 'VALIDATION_FAILED' }>['issues']>().toEqualTypeOf<
      { path: string; message: string }[]
    >()
  })
})
