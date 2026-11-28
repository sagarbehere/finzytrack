// @vitest-environment node
// One frontend error path (L2-05, L8-30; ARCHITECTURE.md §6.2, §6.4; ADR-0039): every failure of a call becomes one
// typed AppError, decoded once in src/api. A valid error envelope keeps the server's words field by field (§4.1's
// envelope; the registry is the single source of codes, so an unknown code or a code with another class is not
// trusted); anything else becomes a client code, never a made-up server code.
import { describe, expect, it } from 'vitest'

import { AppError, decodeError, decodeSuccess, failureOf } from '../../../src/api/errors'

const meta = { request_id: 'req-1', workspace_id: 'ws-a', ledger_version: null, config_version: null }

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    error: {
      code: 'WORKSPACE_NOT_OPEN',
      class: 'conflict',
      message: 'This workspace is not open.',
      data_safe: true,
      next_step: 'Open it from the workspace list.',
      issues: [],
      retryable: false,
      retry_after_ms: null,
      details: { open_workspace: 'ws-b', switching: false },
      correlation_id: 'req-1',
      ...overrides,
    },
    meta,
  }
}

describe('[L2-05] the error envelope decodes once into one AppError', () => {
  it('keeps every field of a valid envelope', () => {
    const e = decodeError(409, envelope())
    expect(e).toBeInstanceOf(AppError)
    expect(e.code).toBe('WORKSPACE_NOT_OPEN')
    expect(e.errorClass).toBe('conflict')
    expect(e.message).toBe('This workspace is not open.')
    expect(e.dataSafe).toBe(true)
    expect(e.nextStep).toBe('Open it from the workspace list.')
    expect(e.issues).toEqual([])
    expect(e.retryable).toBe(false)
    expect(e.retryAfterMs).toBeNull()
    expect(e.details).toEqual({ open_workspace: 'ws-b', switching: false })
    expect(e.correlationId).toBe('req-1')
    expect(e.status).toBe(409)
  })

  it('keeps located issues and a retry delay', () => {
    const issues = [{ path: '/postings/1/amount', message: 'Not a number.' }]
    const e = decodeError(
      422,
      envelope({ code: 'VALIDATION_FAILED', class: 'invalid', details: null, issues, retryable: false }),
    )
    expect(e.code).toBe('VALIDATION_FAILED')
    expect(e.issues).toEqual(issues)
    const busy = decodeError(
      503,
      envelope({
        code: 'ENGINE_QUEUE_FULL',
        class: 'unavailable',
        retryable: true,
        retry_after_ms: 500,
        details: null,
      }),
    )
    expect(busy.retryable).toBe(true)
    expect(busy.retryAfterMs).toBe(500)
  })

  // must-not-pass: what is not a valid envelope never becomes a server code
  it.each([
    ['an unknown code', envelope({ code: 'NO_SUCH_CODE' })],
    ['an unknown code with no class', envelope({ code: 'NO_SUCH_CODE', class: undefined })],
    ['an inherited name as the code', envelope({ code: 'toString', class: undefined })],
    ['a code with another class', envelope({ class: 'internal' })],
    ['a missing message', envelope({ message: undefined })],
    ['a message that is not text', envelope({ message: 42 })],
    ['data_safe that is not a boolean', envelope({ data_safe: 'yes' })],
    ['issues that are not a list', envelope({ issues: 'none' })],
    ['an issue without a path', envelope({ issues: [{ message: 'x' }] })],
    ['a retry delay that is not a whole number', envelope({ retry_after_ms: 1.5 })],
    ['a missing correlation id', envelope({ correlation_id: undefined })],
    ['details that are not an object', envelope({ details: 'x' })],
    ['no error object', { meta }],
    ['an HTML page', '<html><body>Bad gateway</body></html>'],
    ['nothing', undefined],
    ['null', null],
  ])('turns %s into CLIENT_DECODE', (_label, body) => {
    const e = decodeError(500, body)
    expect(e.code).toBe('CLIENT_DECODE')
    expect(e.errorClass).toBe('internal')
    expect(e.dataSafe).toBe(false)
    expect(e.status).toBe(500)
  })

  it('never takes a message from an invalid body', () => {
    const e = decodeError(500, envelope({ class: 'internal', message: 'injected words' }))
    expect(e.message).not.toContain('injected')
  })
})

describe('[L2-06] a success answer is the envelope', () => {
  it('unwraps data and meta', () => {
    expect(decodeSuccess(200, { data: { status: 'ok' }, meta })).toEqual({ data: { status: 'ok' }, meta })
  })

  it.each([
    ['no data', { meta }],
    ['no meta', { data: 1 }],
    ['a meta without request_id', { data: 1, meta: { ...meta, request_id: undefined } }],
    ['a meta with a workspace id that is not text', { data: 1, meta: { ...meta, workspace_id: 7 } }],
    ['a meta missing ledger_version', { data: 1, meta: { request_id: 'r', workspace_id: null, config_version: null } }],
    ['text', 'ok'],
  ])('refuses %s as CLIENT_DECODE', (_label, body) => {
    expect(() => decodeSuccess(200, body)).toThrow(AppError)
    try {
      decodeSuccess(200, body)
    } catch (e) {
      expect((e as AppError).code).toBe('CLIENT_DECODE')
    }
  })
})

describe('[L2-05] a call that got no answer', () => {
  it('is CLIENT_ABORTED when its signal was aborted', () => {
    const c = new AbortController()
    c.abort()
    const e = failureOf(new DOMException('The operation was aborted.', 'AbortError'), c.signal)
    expect(e.code).toBe('CLIENT_ABORTED')
    expect(e.errorClass).toBe('aborted')
  })

  it('is CLIENT_NETWORK when the request failed', () => {
    const e = failureOf(new TypeError('Load failed'), undefined)
    expect(e.code).toBe('CLIENT_NETWORK')
    expect(e.errorClass).toBe('unavailable')
    expect(e.retryable).toBe(true)
    // the client cannot know whether a sent request ran, so it claims nothing about the data
    expect(e.dataSafe).toBe(false)
  })

  it('is CLIENT_DECODE when the answer was not JSON', () => {
    expect(failureOf(new SyntaxError('Unexpected token <'), undefined).code).toBe('CLIENT_DECODE')
  })

  it('passes an AppError through unchanged', () => {
    const e = decodeError(409, envelope())
    expect(failureOf(e, undefined)).toBe(e)
  })

  it('is CLIENT_UNEXPECTED for anything else, keeping no foreign message', () => {
    const e = failureOf(new Error('secret detail'), undefined)
    expect(e.code).toBe('CLIENT_UNEXPECTED')
    expect(e.message).not.toContain('secret')
  })
})
