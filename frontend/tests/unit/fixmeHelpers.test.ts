// The pending-test helpers (S0029, S0032). The Playwright helper carries the backlog row of a pending flow as an
// annotation. The vitest helper hands its runner the body `expectedToFail` wraps: that body rejects only with
// `PlannedMissing` from `planned()`. Once the planned file exists, any other error is a real failure (seeded plants
// G02/G03, a confirm dialog that focuses Delete), so the wrapped body resolves, which the runner reports as red. The
// helper's one runner call, and that it passes the wrapped body, are pinned by scripts/tests/test_check_invariants.py
// and scripts/tests/test_pending_helper.py, which can name the runner markers without tripping the scan of the test
// trees.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ANNOTATION_TYPE, fixme as flowPending } from '../../e2e/fixme'
import { expectedToFail, planned, PlannedMissing } from '../fixme'

const meta = { id: 'L9-01', due: 'M2', reason: 'capability not built' }
// A file that "exists" for planned()'s check but cannot be imported: a built module whose import fails.
const BROKEN = '/src/components/app/ExistsButBroken'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('pending-flow helper', () => {
  it('annotates a pending Playwright flow with its backlog row', () => {
    expect(flowPending({ id: 'BL-0068', due: 'M6', reason: 'capability not built' })).toEqual({
      annotation: { type: ANNOTATION_TYPE, description: 'BL-0068 due M6: capability not built' },
    })
  })
})

describe('planned()', () => {
  it('reports a file nobody has built as PlannedMissing', async () => {
    await expect(planned('/src/components/app/NotBuiltYet.vue')).rejects.toBeInstanceOf(PlannedMissing)
    await expect(planned('/src/components/app/notBuiltYet')).rejects.toBeInstanceOf(PlannedMissing)
  })

  it('loads a file that exists, named as Vite resolves it', async () => {
    expect(await planned<{ default: unknown }>('/src/App.vue')).toHaveProperty('default')
    vi.spyOn(console, 'warn').mockImplementation(() => undefined) // main.ts mounts on a page with no #app
    expect(await planned<object>('/src/main')).toBeDefined()
    await expect(planned('/src/App')).rejects.toBeInstanceOf(PlannedMissing) // Vite needs `.vue` spelled out
  })

  it('passes on the error of a file that exists but fails to import, never calling it missing', async () => {
    const exists = (file: string) => file.endsWith(`${BROKEN.slice(1)}.ts`)
    const error: unknown = await planned(BROKEN, exists).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(PlannedMissing)
  })

  it('refuses a path outside /src/ without calling it missing', async () => {
    await expect(planned('../package.json')).rejects.toThrow('is not a /src/ path')
  })
})

describe('the wrapped body rejects only while the planned module is missing', () => {
  it('rejects when the planned module is missing (the expected outcome)', async () => {
    const run = expectedToFail(meta, async () => {
      await planned('/src/components/app/NotBuiltYet.vue')
    })
    await expect(run()).rejects.toBeInstanceOf(PlannedMissing)
  })

  it('resolves and reports the error when the module exists but the body throws', async () => {
    const report = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const run = expectedToFail(meta, async () => {
      await planned('/src/App.vue')
      expect('Delete').toBe('Cancel') // a buggy first implementation
    })
    await expect(run()).resolves.toBeUndefined()
    expect(report).toHaveBeenCalledWith(
      'pending L9-01: the planned module exists, so this failure is real:',
      expect.any(Error),
    )
  })

  it.each([
    ['a hand-made error named like the planned one', () => Object.assign(new Error('x'), { name: 'PlannedMissing' })],
    ['a failed dynamic import', () => new TypeError('Failed to fetch dynamically imported module: /src/x.ts')],
    ['a type error', () => new TypeError('focus is not a function')],
  ])('resolves on %s', async (_label, error) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    await expect(expectedToFail(meta, () => Promise.reject(error()))()).resolves.toBeUndefined()
  })

  it('resolves when the body passes', async () => {
    await expect(expectedToFail(meta, () => Promise.resolve())()).resolves.toBeUndefined()
  })
})
