// P1 integrity tests for the Transactions edit buffer (review:27 §1(d), Appendix B: green in M5): L8-01, L8-02,
// L8.M-1.
//
// Spec: review:L8 L8-01 (re-running the query replaced the rows with no dirty check; the fix wraps the filter-apply
// command in the unsaved guard), L8-02 (edits typed during Save were marked saved but never sent; the fix:
// `commitSaved(sentIds, serverRows)` rebaselines only the ids sent), review:L8-verif M-1 (undo of op1 then op2
// resurrected op1's payee with the row marked modified and the log empty; the fix: per-operation inverse patches),
// review:L8 §1.1 A10/A12 (the buffer's verbs), ADR-0035 (one `useUnsavedGuard`), INV-16.
//
// Interface: `useEditBuffer` in `features/transactions/hooks/` (ARCHITECTURE.md §3.1). Assumed, not decided: the
// buffer's method names below (from review:L8 §1.1: `load`, `commitSaved`), the op shape for bulk edits, and
// `useFilterApply({ buffer, confirmDiscard, run })` as the filter-apply command the view uses.
import { describe, expect } from 'vitest'

import { fixme, planned } from '../../fixme'

interface Row {
  id: string
  payee: string
  narration: string
}
interface BulkOp {
  label: string
  patch: Record<string, Partial<Omit<Row, 'id'>>>
}
interface EditBuffer {
  load(rows: Row[]): void
  edit(id: string, patch: Partial<Omit<Row, 'id'>>): void
  rows(): Row[]
  modifiedIds(): string[]
  commitSaved(sentIds: string[], serverRows: Row[]): void
  apply(op: BulkOp): string
  undo(opId: string): void
  log(): string[]
}
interface FilterApply {
  apply(filter: Record<string, string>): Promise<boolean>
  filter(): Record<string, string>
}
type BufferModule = { useEditBuffer: () => EditBuffer }
type FilterModule = {
  useFilterApply: (deps: {
    buffer: EditBuffer
    confirmDiscard: () => Promise<boolean>
    run: (filter: Record<string, string>) => Promise<Row[]>
  }) => FilterApply
}

const BUFFER = '/src/features/transactions/hooks/useEditBuffer'
const FILTER = '/src/features/transactions/hooks/useFilterApply'
const loaded = (): Row[] => [
  { id: 't1', payee: 'Cafe', narration: 'n1' },
  { id: 't2', payee: 'Grocer', narration: 'n2' },
]
const row = (b: EditBuffer, id: string): Row | undefined => b.rows().find((r) => r.id === id)

describe('Transactions edit buffer (P1)', () => {
  fixme(
    { id: 'L8-01', due: 'M5', reason: 'capability not built' },
    're-running the query asks before it discards unsaved edits',
    async () => {
      const { useEditBuffer } = await planned<BufferModule>(BUFFER)
      const { useFilterApply } = await planned<FilterModule>(FILTER)
      const buffer = useEditBuffer()
      buffer.load(loaded())
      buffer.edit('t1', { payee: 'Cafe Mocha' })
      const runs: Record<string, string>[] = []
      let answer = false
      const asked: string[] = []
      const cmd = useFilterApply({
        buffer,
        confirmDiscard: () => {
          asked.push('discard?')
          return Promise.resolve(answer)
        },
        run: (filter) => {
          runs.push(filter)
          return Promise.resolve([{ id: 't9', payee: 'Other', narration: '' }])
        },
      })

      expect(await cmd.apply({ accountContains: 'Food' })).toBe(false) // Cancel: nothing re-runs
      expect(runs).toEqual([])
      expect(row(buffer, 't1')?.payee).toBe('Cafe Mocha')
      expect(cmd.filter()).not.toEqual({ accountContains: 'Food' })
      answer = true
      expect(await cmd.apply({ accountContains: 'Food' })).toBe(true) // Discard: re-runs and loads
      expect(runs).toEqual([{ accountContains: 'Food' }])
      expect(buffer.rows().map((r) => r.id)).toEqual(['t9'])
      expect(asked).toHaveLength(2)
    },
  )

  fixme(
    { id: 'L8-02', due: 'M5', reason: 'capability not built' },
    'edits typed while Save is in flight stay modified and are sent next time',
    async () => {
      const { useEditBuffer } = await planned<BufferModule>(BUFFER)
      const buffer = useEditBuffer()
      buffer.load(loaded())
      buffer.edit('t1', { payee: 'Cafe Mocha' })
      const sent = buffer.modifiedIds() // the Save request carries t1
      buffer.edit('t2', { payee: 'Grocer Ltd' }) // typed during the request

      buffer.commitSaved(sent, [{ id: 't1', payee: 'Cafe Mocha', narration: 'n1' }])

      expect(sent).toEqual(['t1'])
      expect(buffer.modifiedIds()).toEqual(['t2'])
      expect(row(buffer, 't2')?.payee).toBe('Grocer Ltd')
    },
  )

  fixme(
    { id: 'L8.M-1', due: 'M5', reason: 'capability not built' },
    'undo reverses only its own operation and undone changes never come back',
    async () => {
      const { useEditBuffer } = await planned<BufferModule>(BUFFER)
      const buffer = useEditBuffer()
      buffer.load(loaded())
      const op1 = buffer.apply({ label: 'Set payee', patch: { t1: { payee: 'A' } } })
      const op2 = buffer.apply({ label: 'Set narration', patch: { t1: { narration: 'N' }, t2: { narration: 'N' } } })

      buffer.undo(op1)
      expect(row(buffer, 't1')).toEqual({ id: 't1', payee: 'Cafe', narration: 'N' }) // op2 is kept
      buffer.undo(op2)

      expect(buffer.rows()).toEqual(loaded()) // review:L8-verif M-1: t1's payee must not come back as 'A'
      expect(buffer.modifiedIds()).toEqual([])
      expect(buffer.log()).toEqual([])
    },
  )
})
