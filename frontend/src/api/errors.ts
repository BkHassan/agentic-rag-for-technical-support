import type { SupportTicket } from './types'

export type ApiErrorKind =
  /** HTTP 422: the request body failed backend validation. */
  | 'validation'
  /** HTTP 400: rejected request or incomplete backend configuration. */
  | 'bad_request'
  /** HTTP 429: Gemini rate limit or daily quota reached. */
  | 'rate_limit'
  /** HTTP 502 with a backend body: Gemini returned an error. */
  | 'upstream'
  /** HTTP 503: model busy or agent runtime unavailable. */
  | 'unavailable'
  /** HTTP 500: unexpected backend failure. */
  | 'server'
  /** Any other non-2xx status. */
  | 'http'
  /** The backend could not be reached (connection failure or proxy gateway error). */
  | 'network'
  /** No response within the client timeout. */
  | 'timeout'
  /** 2xx response whose body does not match the AgentResponse schema. */
  | 'invalid_response'
  /** A failure that did not come from the request itself. */
  | 'unknown'

export type TicketField = keyof SupportTicket

interface ApiErrorInit {
  kind: ApiErrorKind
  message: string
  status?: number
  fieldErrors?: Partial<Record<TicketField, string>>
  retryAfterSeconds?: number
}

/**
 * A request failure with a message that is safe to show. Backend `detail` text is
 * never surfaced verbatim: it can contain upstream Gemini messages or configuration
 * details, so only field validation messages and a retry delay are extracted.
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | undefined
  readonly fieldErrors: Partial<Record<TicketField, string>>
  readonly retryAfterSeconds: number | undefined

  constructor({ kind, message, status, fieldErrors = {}, retryAfterSeconds }: ApiErrorInit) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.fieldErrors = fieldErrors
    this.retryAfterSeconds = retryAfterSeconds
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

const TICKET_FIELDS: ReadonlyArray<TicketField> = ['ticket_id', 'description']
const MAX_FIELD_MESSAGE_LENGTH = 160

function detailOf(body: unknown): unknown {
  return body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : undefined
}

/** FastAPI 422 body: `{"detail": [{"type", "loc": ["body", field], "msg", ...}]}`. */
function fieldErrorsFrom(detail: unknown): Partial<Record<TicketField, string>> {
  const errors: Partial<Record<TicketField, string>> = {}
  if (!Array.isArray(detail)) return errors
  for (const item of detail) {
    if (!item || typeof item !== 'object') continue
    const { loc, msg, type } = item as { loc?: unknown; msg?: unknown; type?: unknown }
    if (!Array.isArray(loc) || loc[0] !== 'body') continue
    const field = TICKET_FIELDS.find((name) => name === loc[1])
    if (!field || errors[field]) continue
    if (type === 'missing' || type === 'string_too_short') {
      errors[field] = 'This field is required.'
    } else if (typeof msg === 'string' && msg.trim()) {
      errors[field] = msg.trim().slice(0, MAX_FIELD_MESSAGE_LENGTH)
    } else {
      errors[field] = 'This value is not valid.'
    }
  }
  return errors
}

const UNIT_SECONDS: Record<string, number> = { h: 3600, m: 60, s: 1, ms: 0.001 }

/**
 * The 429 detail embeds Gemini's message with a Go-style duration, e.g.
 * "Please retry in 41.2s." or "Please retry in 7h9m23.666690483s."
 */
function retryAfterFrom(detail: unknown): number | undefined {
  if (typeof detail !== 'string') return undefined
  const match = /retry in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)/i.exec(detail)
  if (!match) return undefined
  let total = 0
  for (const [, value, unit] of match[1].toLowerCase().matchAll(/(\d+(?:\.\d+)?)(ms|h|m|s)/g)) {
    total += Number(value) * UNIT_SECONDS[unit]
  }
  const seconds = Math.ceil(total)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : undefined
}

export function errorFromResponse(status: number, body: unknown): ApiError {
  const detail = detailOf(body)

  switch (status) {
    case 422: {
      return new ApiError({
        kind: 'validation',
        status,
        message: 'The backend rejected the ticket because some fields are invalid.',
        fieldErrors: fieldErrorsFrom(detail),
      })
    }
    case 400:
      return new ApiError({
        kind: 'bad_request',
        status,
        message:
          'The backend could not process this request. Its configuration may be incomplete (for example, a missing Gemini API key). Check the backend logs.',
      })
    case 429: {
      const retryAfterSeconds = retryAfterFrom(detail)
      return new ApiError({
        kind: 'rate_limit',
        status,
        retryAfterSeconds,
        message: 'The Gemini rate limit or daily quota has been reached. Wait before trying again.',
      })
    }
    case 502:
    case 504:
      // FastAPI always answers with a JSON detail; an empty body comes from a proxy
      // that could not reach the backend.
      if (detail === undefined) {
        return new ApiError({
          kind: 'network',
          status,
          message: 'The backend is not reachable. Check that the FastAPI server is running.',
        })
      }
      return new ApiError({
        kind: 'upstream',
        status,
        message: 'The language model service returned an error. Try again in a moment.',
      })
    case 503:
      return new ApiError({
        kind: 'unavailable',
        status,
        message: 'The analysis service is temporarily unavailable, often because the model is busy. Try again shortly.',
      })
    case 500:
      return new ApiError({
        kind: 'server',
        status,
        message: 'The backend hit an unexpected error while analyzing the ticket.',
      })
    default:
      return new ApiError({
        kind: 'http',
        status,
        message:
          status === 404
            ? 'The analysis endpoint was not found. Check NEXT_PUBLIC_API_BASE_URL.'
            : `The request failed with HTTP ${status}.`,
      })
  }
}
