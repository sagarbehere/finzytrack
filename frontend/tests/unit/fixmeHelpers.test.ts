// The Playwright helper (S0029) carries the backlog row of a pending flow as an annotation. The vitest helper's one
// runner call and this annotation's type are pinned by scripts/tests/test_check_invariants.py, which can name the
// runner markers without tripping the scan of the test trees.
import { describe, expect, it } from 'vitest'

import { ANNOTATION_TYPE, fixme as flowPending } from '../../e2e/fixme'

describe('pending-flow helper', () => {
  it('annotates a pending Playwright flow with its backlog row', () => {
    expect(flowPending({ id: 'BL-0068', due: 'M6', reason: 'capability not built' })).toEqual({
      annotation: { type: ANNOTATION_TYPE, description: 'BL-0068 due M6: capability not built' },
    })
  })
})
