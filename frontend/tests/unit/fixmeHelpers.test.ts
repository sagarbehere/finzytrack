// @vitest-environment node
// The fixme helpers (S0029): a vitest fixme test runs as `it.fails` (its body must fail until the capability lands),
// and a Playwright fixme carries its backlog row as an annotation. `scripts/check_invariants.py` checks the markers'
// IDs; these tests pin what the helpers do with them.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { fixme as flowFixme } from '../../e2e/fixme'

describe('fixme helpers', () => {
  it('runs a vitest fixme test as an expected failure, never as a skip', () => {
    // vitest's own `it` cannot be spied on (its properties are not configurable), so the helper's source is pinned:
    // its one runner call is `it.fails`, which fails the run once the body passes.
    const source = readFileSync(fileURLToPath(new URL('../fixme.ts', import.meta.url)), 'utf8')
    expect(source.match(/\b(?:it|test|describe)\.\w+\(/g)).toEqual(['it.fails('])
  })

  it('annotates a Playwright fixme flow with its backlog row', () => {
    expect(flowFixme({ id: 'BL-0068', due: 'M6', reason: 'capability not built' })).toEqual({
      annotation: { type: 'fixme', description: 'BL-0068 due M6: capability not built' },
    })
  })
})
