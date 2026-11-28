// One frontend error path (L2-05, L8-30; ARCHITECTURE.md §6.2, §6.4; ADR-0039; S0166). Every failure of a call is
// decoded here, once, into one AppError; nothing outside src/api reads a response body. A valid error envelope keeps
// the server's words (the registry is the single source of codes and texts); anything else becomes a client code
// with our own words, so a broken or foreign body can never put its text in front of the user.
// Stated limit: `details` is checked to be an object or null, not against its code's own schema; the generated
// decoders bring that (`decode.gen.ts`, BL-0107).
import type { components } from './generated/openapi'
import { serverErrorClasses } from './generated/errors.gen'

type ServerError = components['schemas']['ErrorEnvelope']['error']
export type Meta = components['schemas']['Meta']
export type Issue = components['schemas']['Issue']

type ServerErrorCode = ServerError['code']
type ServerErrorClass = ServerError['class']

// Codes the client makes itself. A client error claims nothing about the user's data (dataSafe false): the client
// cannot know whether a request it sent ran.
const clientErrors = {
  CLIENT_NETWORK: {
    errorClass: 'unavailable',
    retryable: true,
    message: 'Finzytrack could not get an answer from its server.',
    nextStep: 'Check that Finzytrack is still running, then try again.',
  },
  CLIENT_DECODE: {
    errorClass: 'internal',
    retryable: false,
    message: 'Finzytrack received an answer it could not read.',
    nextStep: 'Reload the window. If it happens again, report a problem.',
  },
  CLIENT_WRONG_WORKSPACE: {
    errorClass: 'conflict',
    retryable: false,
    message: 'The answer came from another workspace than the one this window shows.',
    nextStep: 'Reload the window.',
  },
  CLIENT_UNEXPECTED: {
    errorClass: 'internal',
    retryable: false,
    message: 'Something went wrong in Finzytrack.',
    nextStep: 'Reload the window. If it happens again, report a problem.',
  },
  CLIENT_ABORTED: {
    errorClass: 'aborted',
    retryable: false,
    message: 'The request was cancelled.',
    nextStep: 'No action is needed.',
  },
} as const

export type ClientErrorCode = keyof typeof clientErrors
export type ErrorCode = ServerErrorCode | ClientErrorCode
export type ErrorClass = ServerErrorClass | (typeof clientErrors)[ClientErrorCode]['errorClass']

interface AppErrorFields {
  code: ErrorCode
  errorClass: ErrorClass
  message: string
  dataSafe: boolean
  nextStep: string
  issues: readonly Issue[]
  retryable: boolean
  retryAfterMs: number | null
  details: unknown
  correlationId: string | null
  status: number | null
}

export class AppError extends Error {
  readonly code: ErrorCode
  readonly errorClass: ErrorClass
  readonly dataSafe: boolean
  readonly nextStep: string
  readonly issues: readonly Issue[]
  readonly retryable: boolean
  readonly retryAfterMs: number | null
  readonly details: unknown
  readonly correlationId: string | null
  /** The HTTP status of the answer, or null when there was none. */
  readonly status: number | null

  constructor(fields: AppErrorFields) {
    super(fields.message)
    this.name = 'AppError'
    this.code = fields.code
    this.errorClass = fields.errorClass
    this.dataSafe = fields.dataSafe
    this.nextStep = fields.nextStep
    this.issues = fields.issues
    this.retryable = fields.retryable
    this.retryAfterMs = fields.retryAfterMs
    this.details = fields.details
    this.correlationId = fields.correlationId
    this.status = fields.status
  }
}

export function clientError(code: ClientErrorCode, status: number | null = null): AppError {
  const c = clientErrors[code]
  return new AppError({
    code,
    errorClass: c.errorClass,
    message: c.message,
    dataSafe: false,
    nextStep: c.nextStep,
    issues: [],
    retryable: c.retryable,
    retryAfterMs: null,
    details: null,
    correlationId: null,
    status,
  })
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isText = (v: unknown): v is string => typeof v === 'string'
const isTextOrNull = (v: unknown): v is string | null => v === null || typeof v === 'string'
const isIssue = (v: unknown): v is Issue => isRecord(v) && isText(v['path']) && isText(v['message'])
const isServerCode = (v: unknown): v is ServerErrorCode => isText(v) && Object.hasOwn(serverErrorClasses, v)

function isMeta(v: unknown): v is Meta {
  return (
    isRecord(v) &&
    isText(v['request_id']) &&
    'workspace_id' in v &&
    isTextOrNull(v['workspace_id']) &&
    'ledger_version' in v &&
    isTextOrNull(v['ledger_version']) &&
    'config_version' in v &&
    isTextOrNull(v['config_version'])
  )
}

/** The AppError of a non-2xx answer whose body was read as JSON (`undefined` when it was not JSON). */
export function decodeError(status: number, body: unknown): AppError {
  const e = isRecord(body) ? body['error'] : undefined
  if (!isRecord(e)) return clientError('CLIENT_DECODE', status)
  const code = e['code']
  const retryAfter = e['retry_after_ms']
  const issues = e['issues']
  const details = e['details']
  const valid =
    isServerCode(code) &&
    e['class'] === serverErrorClasses[code] &&
    isText(e['message']) &&
    typeof e['data_safe'] === 'boolean' &&
    isText(e['next_step']) &&
    Array.isArray(issues) &&
    issues.every(isIssue) &&
    typeof e['retryable'] === 'boolean' &&
    (retryAfter === null || Number.isSafeInteger(retryAfter)) &&
    'details' in e &&
    (details === null || isRecord(details)) &&
    isText(e['correlation_id'])
  if (!valid) return clientError('CLIENT_DECODE', status)
  return new AppError({
    code,
    errorClass: serverErrorClasses[code],
    message: e['message'] as string,
    dataSafe: e['data_safe'] as boolean,
    nextStep: e['next_step'] as string,
    issues,
    retryable: e['retryable'] as boolean,
    retryAfterMs: retryAfter as number | null,
    details,
    correlationId: e['correlation_id'] as string,
    status,
  })
}

/** The `{data, meta}` of a 2xx answer; anything else is CLIENT_DECODE. */
export function decodeSuccess(status: number, body: unknown): { data: unknown; meta: Meta } {
  if (!isRecord(body) || !('data' in body) || !isMeta(body['meta'])) throw clientError('CLIENT_DECODE', status)
  return { data: body['data'], meta: body['meta'] }
}

/** The AppError of a call that threw before or while reading its answer. */
export function failureOf(thrown: unknown, signal: AbortSignal | undefined): AppError {
  if (thrown instanceof AppError) return thrown
  if (signal?.aborted === true) return clientError('CLIENT_ABORTED')
  if (thrown instanceof SyntaxError) return clientError('CLIENT_DECODE')
  if (thrown instanceof TypeError) return clientError('CLIENT_NETWORK')
  return clientError('CLIENT_UNEXPECTED')
}
