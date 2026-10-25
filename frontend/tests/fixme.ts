// Tests written before their capability exists (S0029; review:27 §1(d)). `fixme` runs the body as vitest's
// `it.fails`: it must fail until the capability lands, and when it passes the run turns red, so the landing slice
// removes the marker and closes the backlog row. `scripts/check_invariants.py` checks that `id` names an open
// backlog row whose milestone is `due`; only this helper may call `it.fails` in the unit tests.
import { it } from 'vitest'

export interface FixmeMeta {
  id: string
  due: string
  reason: string
}

export function fixme(meta: FixmeMeta, title: string, body: () => Promise<void>): void {
  it.fails(`${title} [fixme ${meta.id}, due ${meta.due}: ${meta.reason}]`, body)
}

// A module the decided design names but no slice has built yet, by its path from the frontend root
// (`/src/...`). When it lands, the landing slice replaces the call with a static import.
export async function planned<T>(path: string): Promise<T> {
  const mod: unknown = await import(/* @vite-ignore */ path)
  return mod as T
}
