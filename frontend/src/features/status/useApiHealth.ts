import { useCallback, useEffect, useRef, useState } from 'react'
import { checkHealth, type HealthResult } from '../../api/health'

export type ApiHealthStatus = 'checking' | 'online' | 'offline'

export interface ApiHealth {
  status: ApiHealthStatus
  /** Why the last check failed; null when online or not yet checked. */
  reason: string | null
  /** Time of the last completed check; null until the first one finishes. */
  checkedAt: Date | null
  recheck: () => void
}

const POLL_INTERVAL_MS = 30_000

export function useApiHealth(): ApiHealth {
  const [status, setStatus] = useState<ApiHealthStatus>('checking')
  const [reason, setReason] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)
  const inFlight = useRef<AbortController | null>(null)

  const applyResult = useCallback((result: HealthResult) => {
    setStatus(result.ok ? 'online' : 'offline')
    setReason(result.ok ? null : result.reason)
    setCheckedAt(new Date())
  }, [])

  const run = useCallback(() => {
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    checkHealth(controller.signal).then(applyResult, () => {
      // Aborted by a newer check or unmount; that check owns the state.
    })
  }, [applyResult])

  useEffect(() => {
    run()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') run()
    }, POLL_INTERVAL_MS)
    return () => {
      window.clearInterval(timer)
      inFlight.current?.abort()
    }
  }, [run])

  // Background polls keep the last verified status until the new result arrives.
  const recheck = useCallback(() => {
    setStatus('checking')
    run()
  }, [run])

  return { status, reason, checkedAt, recheck }
}
