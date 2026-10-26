import { apiUrl } from './config'

export type HealthResult =
  | { ok: true }
  | { ok: false; reason: string }

const HEALTH_TIMEOUT_MS = 5000

/**
 * GET /health returns {"status": "ok"} when the FastAPI process is up.
 * It does not exercise Gemini or the document index.
 */
export async function checkHealth(signal?: AbortSignal): Promise<HealthResult> {
  const timeout = AbortSignal.timeout(HEALTH_TIMEOUT_MS)
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
  try {
    const response = await fetch(apiUrl('/health'), {
      headers: { Accept: 'application/json' },
      signal: combined,
    })
    if (response.status === 502 || response.status === 504) {
      return { ok: false, reason: `The API is not reachable (HTTP ${response.status} from the proxy).` }
    }
    if (!response.ok) {
      return { ok: false, reason: `Health check returned HTTP ${response.status}.` }
    }
    const body: unknown = await response.json().catch(() => null)
    if (body && typeof body === 'object' && (body as { status?: unknown }).status === 'ok') {
      return { ok: true }
    }
    return { ok: false, reason: 'Health check returned an unexpected response.' }
  } catch (error) {
    if (signal?.aborted) throw error
    if (timeout.aborted) {
      return { ok: false, reason: `No response within ${HEALTH_TIMEOUT_MS / 1000} seconds.` }
    }
    return { ok: false, reason: 'Could not reach the API.' }
  }
}
