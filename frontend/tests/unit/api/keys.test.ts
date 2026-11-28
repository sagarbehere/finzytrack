// @vitest-environment node
// Query keys come from the operation's declared scope, never by hand (ADR-0037, ADR-0038; ARCHITECTURE.md §3.2;
// L2-03): app-scoped keys start ['app'], workspace keys ['ws', ws, scope], with `ws` from the same path parameter
// the request sends (ADR-0018), so two workspaces never share a key below ['ws'] (INV-28). The scope table is
// generated from the contract's x-ft-scope (frontend/scripts/gen-api-extras.mjs), and the generator fails closed.
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

import { keyFor, queryKey } from '../../../src/api/keys'

const table = {
  '/health': { get: { scope: 'app' } },
  '/api/w/{ws}/accounts': { get: { scope: 'ledger' } },
  '/api/w/{ws}/settings': { get: { scope: 'config' } },
  '/api/w/{ws}/plans/{plan_id}:confirm': { post: { scope: 'ledger' } },
} as const

describe('[L2-03][INV-28] query keys are scoped by the declared scope', () => {
  it('starts an app-scoped key with app', () => {
    expect(keyFor(table, '/health', {})).toEqual(['app', '/health', null])
  })

  it('starts a workspace key with ws, the workspace and the scope', () => {
    const key = keyFor(table, '/api/w/{ws}/accounts', { path: { ws: 'ws-a' }, query: { cursor: 'c1' } })
    expect(key).toEqual(['ws', 'ws-a', 'ledger', '/api/w/{ws}/accounts', {}, { cursor: 'c1' }])
    expect(keyFor(table, '/api/w/{ws}/settings', { path: { ws: 'ws-a' } }).slice(0, 3)).toEqual([
      'ws',
      'ws-a',
      'config',
    ])
  })

  it('gives two workspaces keys with no common prefix below ws', () => {
    const a = keyFor(table, '/api/w/{ws}/accounts', { path: { ws: 'ws-a' } })
    const b = keyFor(table, '/api/w/{ws}/accounts', { path: { ws: 'ws-b' } })
    expect(a[1]).toBe('ws-a')
    expect(b[1]).toBe('ws-b')
    expect(a.slice(0, 2)).not.toEqual(b.slice(0, 2))
  })

  // must-not-pass
  it.each([
    ['a workspace path without its ws', '/api/w/{ws}/accounts', {}],
    ['an empty ws', '/api/w/{ws}/accounts', { path: { ws: '' } }],
    ['a ws that is not text', '/api/w/{ws}/accounts', { path: { ws: 7 } }],
    ['an undeclared path', '/api/w/{ws}/nothing', { path: { ws: 'ws-a' } }],
    ['a path with no GET', '/api/w/{ws}/plans/{plan_id}:confirm', { path: { ws: 'ws-a', plan_id: 'p' } }],
    ['an inherited name', 'toString', {}],
  ])('refuses %s', (_label, path, params) => {
    expect(() => keyFor(table, path, params)).toThrow()
  })

  it("builds the contract's own keys from the generated table", () => {
    expect(queryKey('/health', {})).toEqual(['app', '/health', null])
  })
})

const root = fileURLToPath(new URL('../../..', import.meta.url))
const generator = join(root, 'scripts/gen-api-extras.mjs')
const scratch = mkdtempSync(join(tmpdir(), 'ft-gen-extras-'))
afterAll(() => {
  rmSync(scratch, { recursive: true, force: true })
})

function generate(doc: unknown, which: 'keys' | 'errors') {
  const file = join(scratch, `contract-${String(Math.random()).slice(2)}.json`)
  writeFileSync(file, JSON.stringify(doc))
  return spawnSync(process.execPath, [generator, '--contract', file, '--stdout', which], { encoding: 'utf8' })
}

const errorUnion = {
  ErrorEnvelope: { properties: { error: { oneOf: [{ $ref: '#/components/schemas/InternalError' }] } } },
  InternalError: { properties: { code: { const: 'INTERNAL' }, class: { const: 'internal' } } },
}
const op = (scope: unknown, writes: unknown) => ({ 'x-ft-scope': scope, 'x-ft-writes': writes })

describe('[L2-03] the generated tables', () => {
  it('are the committed files, generated from contracts/openapi.json', () => {
    for (const which of ['keys', 'errors'] as const) {
      const fresh = spawnSync(process.execPath, [generator, '--stdout', which], { encoding: 'utf8' })
      expect(fresh.status).toBe(0)
      expect(readFileSync(join(root, `src/api/generated/${which}.gen.ts`), 'utf8')).toBe(fresh.stdout)
      expect(fresh.stdout.split('\n')[0]).toContain('GENERATED')
    }
  })

  it('carry each operation with its scope and writes', () => {
    const out = generate(
      {
        paths: { '/health': { get: op('app', 'none') }, '/api/w/{ws}/accounts': { get: op('ledger', 'none') } },
        components: { schemas: errorUnion },
      },
      'keys',
    )
    expect(out.status).toBe(0)
    expect(out.stdout).toContain("'/api/w/{ws}/accounts': {\n    get: { scope: 'ledger', writes: 'none' },")
    expect(out.stdout).toContain("'/health': {\n    get: { scope: 'app', writes: 'none' },")
  })

  it('carry each error code with its class', () => {
    const out = generate({ paths: {}, components: { schemas: errorUnion } }, 'errors')
    expect(out.status).toBe(0)
    expect(out.stdout).toContain("  INTERNAL: 'internal',")
  })

  // must-not-pass: the generator stops rather than leave an operation or a code out
  it.each([
    ['an operation without x-ft-scope', { '/health': { get: { 'x-ft-writes': 'none' } } }],
    ['an operation with an unknown scope', { '/health': { get: op('global', 'none') } }],
    ['an operation without x-ft-writes', { '/health': { get: { 'x-ft-scope': 'app' } } }],
    ['a ledger operation outside /api/w/{ws}/', { '/api/accounts': { get: op('ledger', 'none') } }],
  ])('refuse %s', (_label, paths) => {
    const out = generate({ paths, components: { schemas: errorUnion } }, 'keys')
    expect(out.status).not.toBe(0)
    expect(out.stdout).toBe('')
  })

  it.each([
    [
      'an error schema without its class',
      { ...errorUnion, InternalError: { properties: { code: { const: 'INTERNAL' } } } },
    ],
    ['no error union', { ErrorEnvelope: { properties: {} } }],
  ])('refuse %s', (_label, schemas) => {
    const out = generate({ paths: {}, components: { schemas } }, 'errors')
    expect(out.status).not.toBe(0)
  })
})
