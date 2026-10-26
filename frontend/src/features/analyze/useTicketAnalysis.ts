import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/errors'
import { analyzeTicket } from '../../api/tickets'
import type { AgentResponse, SupportTicket } from '../../api/types'

export type AnalysisState =
  | { status: 'idle' }
  | { status: 'loading'; ticket: SupportTicket }
  | { status: 'success'; ticket: SupportTicket; response: AgentResponse }
  | { status: 'error'; ticket: SupportTicket; error: ApiError }

export function useTicketAnalysis() {
  const [state, setState] = useState<AnalysisState>({ status: 'idle' })
  const inFlight = useRef<AbortController | null>(null)

  useEffect(() => () => inFlight.current?.abort(), [])

  const submit = useCallback(async (ticket: SupportTicket) => {
    // A ref, not state, so a second submit in the same tick is also ignored.
    if (inFlight.current) return
    const controller = new AbortController()
    inFlight.current = controller
    setState({ status: 'loading', ticket })
    try {
      const response = await analyzeTicket(ticket, { signal: controller.signal })
      setState({ status: 'success', ticket, response })
    } catch (error) {
      if (controller.signal.aborted) return
      setState({
        status: 'error',
        ticket,
        error:
          error instanceof ApiError
            ? error
            : new ApiError({ kind: 'unknown', message: 'The request failed unexpectedly. Try again.' }),
      })
    } finally {
      if (inFlight.current === controller) inFlight.current = null
    }
  }, [])

  /** Returns to the empty state; an in-flight request is cancelled and its result ignored. */
  const reset = useCallback(() => {
    inFlight.current?.abort()
    inFlight.current = null
    setState({ status: 'idle' })
  }, [])

  return { state, submit, reset }
}
