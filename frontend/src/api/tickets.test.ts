import { describe, expect, it, vi } from 'vitest'
import {
  RECORDED_CLARIFICATION_NEEDED,
  RECORDED_FALLBACK,
  RECORDED_GROUNDED_ANSWER,
} from '../test/recordedResponses'
import { ApiError, isAbortError } from './errors'
import { analyzeTicket } from './tickets'

const TICKET = { ticket_id: 'T-DB-001', description: 'The database connection keeps timing out.' }

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status, headers: { 'Content-Type': 'text/plain' } })
}

function fetchReturning(response: Response) {
  return vi.fn<typeof fetch>().mockResolvedValue(response)
}

/** A fetch that never settles on its own and rejects like the platform when aborted. */
function hangingFetch() {
  return vi.fn<typeof fetch>(
    (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }),
  )
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => {
      throw new Error('Expected the request to fail')
    },
    (reason: unknown) => reason,
  )
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

describe('analyzeTicket request', () => {
  it('posts exactly ticket_id and description as JSON to /tickets/analyze', async () => {
    const fetchImpl = fetchReturning(jsonResponse(200, RECORDED_GROUNDED_ANSWER))

    await analyzeTicket(TICKET, { fetchImpl })

    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/tickets/analyze')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json', Accept: 'application/json' })
    expect(JSON.parse(String(init?.body))).toEqual(TICKET)
  })
})

describe('analyzeTicket responses', () => {
  it.each([
    ['grounded_answer', RECORDED_GROUNDED_ANSWER],
    ['clarification_needed', RECORDED_CLARIFICATION_NEEDED],
    ['fallback', RECORDED_FALLBACK],
  ])('parses a recorded %s response unchanged', async (_outcome, recorded) => {
    const result = await analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(200, recorded)) })
    expect(result).toEqual(recorded)
  })

  it('treats omitted defaulted fields as empty, as the schema allows', async () => {
    const minimal = {
      ticket_id: 'T-1',
      category: 'network',
      outcome: 'fallback',
      sufficient_evidence: false,
      fallback_triggered: true,
    }
    const result = await analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(200, minimal)) })
    expect(result).toEqual({
      ...minimal,
      clarifying_questions: [],
      recommendations: [],
      sources: [],
      summary: null,
    })
  })

  it.each([
    ['an unknown outcome', { ...RECORDED_GROUNDED_ANSWER, outcome: 'answered' }],
    ['an unknown category', { ...RECORDED_GROUNDED_ANSWER, category: 'storage' }],
    ['a missing required flag', { ...RECORDED_GROUNDED_ANSWER, sufficient_evidence: undefined }],
    ['a source without a filename', { ...RECORDED_GROUNDED_ANSWER, sources: [{ doc_id: 'x' }] }],
  ])('rejects a 200 body with %s', async (_case, body) => {
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(200, body)) }))
    expect(error.kind).toBe('invalid_response')
  })

  it('rejects a 200 body that is not JSON', async () => {
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(textResponse(200, '<html>')) }))
    expect(error.kind).toBe('invalid_response')
  })
})

describe('analyzeTicket HTTP errors', () => {
  it('maps the real FastAPI 422 body to field errors', async () => {
    const body = {
      detail: [
        {
          type: 'string_too_short',
          loc: ['body', 'description'],
          msg: 'String should have at least 1 character',
          input: '   ',
          ctx: { min_length: 1 },
        },
      ],
    }
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(422, body)) }))
    expect(error.kind).toBe('validation')
    expect(error.status).toBe(422)
    expect(error.fieldErrors).toEqual({ description: 'This field is required.' })
  })

  it('reports a 429 with the retry delay from the Gemini message', async () => {
    const body = {
      detail: 'Gemini API rate limit exceeded. Please wait and retry. Upstream message: Quota exceeded. Please retry in 41.2s.',
    }
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(429, body)) }))
    expect(error.kind).toBe('rate_limit')
    expect(error.retryAfterSeconds).toBe(42)
    expect(error.message).not.toContain('Quota exceeded')
  })

  it.each([
    ['7h9m23.666690483s', 25_764],
    ['2m5s', 125],
    ['500ms', 1],
  ])('parses the Go-style retry duration %s', async (duration, seconds) => {
    // Shape of the real detail returned by the backend when the daily quota is exhausted.
    const body = {
      detail: `Gemini API rate limit exceeded. Please wait and retry. Upstream message: You exceeded your current quota. Please retry in ${duration}.`,
    }
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(429, body)) }))
    expect(error.retryAfterSeconds).toBe(seconds)
  })

  it('does not expose backend detail text for 400 errors', async () => {
    const body = { detail: 'GOOGLE_API_KEY is not set. Add it to /srv/app/.env' }
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(400, body)) }))
    expect(error.kind).toBe('bad_request')
    expect(error.message).not.toMatch(/GOOGLE_API_KEY|\/srv/)
  })

  it('does not expose a stack trace from a 500 body', async () => {
    const body = 'Traceback (most recent call last):\n  File "/srv/app/agent.py", line 1'
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(textResponse(500, body)) }))
    expect(error.kind).toBe('server')
    expect(error.message).not.toMatch(/Traceback|agent\.py/)
  })

  it('distinguishes an unreachable backend (empty proxy 502) from a Gemini 502', async () => {
    const proxy = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(textResponse(502, '')) }))
    expect(proxy.kind).toBe('network')

    const upstream = await failure(
      analyzeTicket(TICKET, {
        fetchImpl: fetchReturning(jsonResponse(502, { detail: 'Gemini API client error: model not found' })),
      }),
    )
    expect(upstream.kind).toBe('upstream')
    expect(upstream.message).not.toContain('model not found')
  })

  it('maps 503 to a temporary unavailability', async () => {
    const body = { detail: 'Upstream Gemini service error: The model is overloaded due to high demand.' }
    const error = await failure(analyzeTicket(TICKET, { fetchImpl: fetchReturning(jsonResponse(503, body)) }))
    expect(error.kind).toBe('unavailable')
    expect(error.message).not.toContain('overloaded')
  })
})

describe('analyzeTicket transport failures', () => {
  it('reports a network failure', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await failure(analyzeTicket(TICKET, { fetchImpl }))
    expect(error.kind).toBe('network')
  })

  it('times out and aborts the request', async () => {
    const fetchImpl = hangingFetch()
    const error = await failure(analyzeTicket(TICKET, { fetchImpl, timeoutMs: 20 }))
    expect(error.kind).toBe('timeout')
    expect(fetchImpl.mock.calls[0][1]?.signal?.aborted).toBe(true)
  })

  it('rethrows the AbortError when the caller cancels', async () => {
    const controller = new AbortController()
    const request = analyzeTicket(TICKET, { fetchImpl: hangingFetch(), signal: controller.signal })
    controller.abort()
    const error = await request.catch((reason: unknown) => reason)
    expect(error).not.toBeInstanceOf(ApiError)
    expect(isAbortError(error)).toBe(true)
  })
})
