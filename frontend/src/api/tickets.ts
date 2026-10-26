import { apiUrl } from './config'
import { ApiError, errorFromResponse } from './errors'
import { parseAgentResponse } from './parseAgentResponse'
import type { AgentResponse, SupportTicket } from './types'

/** Recorded end-to-end runs take 2–46 s (median about 11 s); the backend sets no timeout of its own. */
export const ANALYZE_TIMEOUT_MS = 90_000

interface AnalyzeOptions {
  /** Aborting rejects with the original AbortError so callers can ignore it. */
  signal?: AbortSignal
  timeoutMs?: number
  fetchImpl?: typeof fetch
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

/** POST /tickets/analyze. Resolves with a validated AgentResponse or rejects with ApiError. */
export async function analyzeTicket(ticket: SupportTicket, options: AnalyzeOptions = {}): Promise<AgentResponse> {
  const { signal, timeoutMs = ANALYZE_TIMEOUT_MS, fetchImpl = fetch } = options
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const forwardAbort = () => controller.abort()
  if (signal?.aborted) controller.abort()
  signal?.addEventListener('abort', forwardAbort, { once: true })

  try {
    let response: Response
    let body: unknown
    try {
      response = await fetchImpl(apiUrl('/tickets/analyze'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ ticket_id: ticket.ticket_id, description: ticket.description }),
        signal: controller.signal,
      })
      body = await readJson(response)
    } catch (error) {
      if (timedOut) {
        throw new ApiError({
          kind: 'timeout',
          message: `No response after ${Math.round(timeoutMs / 1000)} seconds. The backend may still be working; try again.`,
        })
      }
      if (signal?.aborted) throw error
      throw new ApiError({
        kind: 'network',
        message: 'Could not reach the backend. Check that the FastAPI server is running and NEXT_PUBLIC_API_BASE_URL is correct.',
      })
    }

    if (!response.ok) throw errorFromResponse(response.status, body)
    return parseAgentResponse(body)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', forwardAbort)
  }
}
