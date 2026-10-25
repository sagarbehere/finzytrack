// Acceptance flows written before their capability exists (S0029; review:27 §1(d)). A spec calls
//   test.fixme(title, fixme({ id, due, reason }), body)
// so Playwright reports the spec's own file and line, with the backlog row as an annotation.
// `scripts/check_invariants.py` checks that `id` names an open backlog row whose milestone is `due`, and refuses any
// other test.fixme, test.fail or it.fails.
import type { TestDetails } from '@playwright/test'

export interface FixmeMeta {
  id: string
  due: string
  reason: string
}

export const ANNOTATION_TYPE = 'fixme'

export function fixme(meta: FixmeMeta): TestDetails {
  return { annotation: { type: ANNOTATION_TYPE, description: `${meta.id} due ${meta.due}: ${meta.reason}` } }
}
