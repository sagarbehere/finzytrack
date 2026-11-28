// One frontend error path: each error reaches the user once (L2-05, L8-30; ARCHITECTURE.md §6.4; ADR-0039). The
// default presentation per class is ARCHITECTURE.md §6.1's table; a cancelled request is never shown.
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'

import { clientError, decodeError } from '../../../src/api/errors'
import { createErrorSink, installErrors, NOTICE_HISTORY, presentationOf, useErrors } from '../../../src/app/errors'

const meta = { request_id: 'r', workspace_id: null, ledger_version: null, config_version: null }
const serverError = (code: string, klass: string) =>
  decodeError(409, {
    error: {
      code,
      class: klass,
      message: 'm',
      data_safe: true,
      next_step: 'n',
      issues: [],
      retryable: false,
      retry_after_ms: null,
      details: null,
      correlation_id: 'r',
    },
    meta,
  })

describe('[L2-05] presentError records each error once', () => {
  it('records an error presented twice once', () => {
    const sink = createErrorSink()
    const e = clientError('CLIENT_NETWORK')
    sink.present(e)
    sink.present(e)
    expect(sink.notices.value).toHaveLength(1)
    expect(sink.notices.value[0]?.error).toBe(e)
  })

  it('records two failures of the same kind twice (each is a new event)', () => {
    const sink = createErrorSink()
    sink.present(clientError('CLIENT_NETWORK'))
    sink.present(clientError('CLIENT_NETWORK'))
    expect(sink.notices.value).toHaveLength(2)
  })

  it('never records a cancelled request', () => {
    const sink = createErrorSink()
    sink.present(clientError('CLIENT_ABORTED'))
    expect(sink.notices.value).toEqual([])
  })

  // S0166 verifier F2: only the API layer knows a failed fetch (and maps it there); anything else thrown is a bug.
  it.each([
    ['a TypeError', new TypeError("Cannot read properties of undefined (reading 'x')")],
    ['a SyntaxError', new SyntaxError('Unexpected token')],
  ])('records %s from a bug as CLIENT_UNEXPECTED, never as a network failure', (_label, thrown) => {
    const sink = createErrorSink()
    sink.present(thrown)
    expect(sink.notices.value[0]?.error.code).toBe('CLIENT_UNEXPECTED')
  })

  it('records a thrown value that is not an AppError once, in our own words', () => {
    const sink = createErrorSink()
    const thrown = new Error('private detail')
    sink.present(thrown)
    sink.present(thrown)
    expect(sink.notices.value).toHaveLength(1)
    expect(sink.notices.value[0]?.error.code).toBe('CLIENT_UNEXPECTED')
    expect(sink.notices.value[0]?.error.message).not.toContain('private')
  })

  it('keeps only the most recent notices', () => {
    const sink = createErrorSink(3)
    for (let i = 0; i < 5; i++) sink.present(clientError('CLIENT_NETWORK'))
    expect(sink.notices.value).toHaveLength(3)
    expect(NOTICE_HISTORY).toBeGreaterThan(0)
    expect(Number.isFinite(NOTICE_HISTORY)).toBe(true)
  })
})

describe('[L2-05] the default presentation follows the class (ARCHITECTURE.md §6.1)', () => {
  it.each([
    ['VALIDATION_FAILED', 'invalid', 'toast'],
    ['WORKSPACE_UNKNOWN', 'not_found', 'toast'],
    ['WORKSPACE_NOT_OPEN', 'conflict', 'modal'],
    ['ACK_REQUIRED', 'contract_refused', 'modal'],
    ['UNPROMOTED_MUTATOR_ON_REAL_LEDGER', 'forbidden', 'modal'],
    ['ENGINE_QUEUE_FULL', 'unavailable', 'toast'],
    ['INTERNAL', 'internal', 'modal'],
  ])('%s (%s) is a %s', (code, klass, presentation) => {
    expect(presentationOf(serverError(code, klass))).toBe(presentation)
  })

  it.each([
    ['CLIENT_NETWORK', 'toast'],
    ['CLIENT_DECODE', 'modal'],
    ['CLIENT_WRONG_WORKSPACE', 'modal'],
    ['CLIENT_UNEXPECTED', 'modal'],
    ['CLIENT_ABORTED', 'none'],
  ] as const)('%s is a %s', (code, presentation) => {
    expect(presentationOf(clientError(code))).toBe(presentation)
  })
})

let seen: unknown
const Probe = defineComponent({
  setup() {
    seen = useErrors()
    return () => h('p')
  },
})

describe('[L2-05] one sink per app', () => {
  it('is what components of that app read', () => {
    const sink = createErrorSink()
    mount(Probe, {
      global: {
        plugins: [
          (app: Parameters<typeof installErrors>[0]) => {
            installErrors(app, sink)
          },
        ],
      },
    })
    expect(seen).toBe(sink)
  })

  it('is refused when none is installed', () => {
    expect(() => mount(Probe)).toThrow('no error sink installed')
  })
})
