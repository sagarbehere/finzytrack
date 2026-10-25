// Tests written before their capability exists (S0029; review:27 §1(d)). `fixme` runs the body as vitest's
// `it.fails`: it must fail until the capability lands, and when it passes the run turns red, so the landing slice
// removes the marker and closes the backlog row. `scripts/check_invariants.py` checks that `id` names an open
// backlog row whose milestone is `due`; only this helper may call `it.fails` in the unit tests.
//
// The only accepted failure is `PlannedMissing` from `planned()` (S0032, seeded plants G02/G03): once the planned file
// exists, any other error is a real failure (a buggy first implementation), so the wrapper reports it and returns,
// which `it.fails` turns red ("expected to fail"). Rethrowing it instead would let `it.fails` pass it.
import { existsSync } from 'node:fs'

import { it } from 'vitest'

export interface FixmeMeta {
  id: string
  due: string
  reason: string
}

export class PlannedMissing extends Error {
  constructor(path: string) {
    super(`planned module ${path} is not built yet`)
    this.name = 'PlannedMissing'
  }
}

// The body `it.fails` runs: it fails (the expected outcome) only with PlannedMissing.
export function expectedToFail(meta: FixmeMeta, body: () => Promise<void>): () => Promise<void> {
  return async () => {
    try {
      await body()
    } catch (error) {
      if (error instanceof PlannedMissing) throw error
      console.error(`pending ${meta.id}: the planned module exists, so this failure is real:`, error)
    }
  }
}

export function fixme(meta: FixmeMeta, title: string, body: () => Promise<void>): void {
  it.fails(`${title} [fixme ${meta.id}, due ${meta.due}: ${meta.reason}]`, expectedToFail(meta, body))
}

const root = `${import.meta.dirname}/../`
// How Vite resolves an import path (its default extensions; a `.vue` file is named with its extension).
const candidates = (path: string): string[] => [path, `${path}.ts`, `${path}/index.ts`]

// A module the decided design names but no slice has built yet, by its path from the frontend root
// (`/src/...`). When it lands, the landing slice replaces the call with a static import. PlannedMissing until the
// file exists; after that, whatever the import does (an error in the module is a real failure). `exists` is for the
// helper's own test only.
export async function planned<T>(path: string, exists: (file: string) => boolean = existsSync): Promise<T> {
  if (!path.startsWith('/src/')) throw new Error(`planned(): ${path} is not a /src/ path`)
  if (!candidates(path).some((p) => exists(`${root}${p.slice(1)}`))) throw new PlannedMissing(path)
  const mod: unknown = await import(/* @vite-ignore */ path)
  return mod as T
}
