import { useEffect, useRef } from 'react'
import { FileSearch } from 'lucide-react'
import { Card, CardHeader, EmptyState } from '../../components/ui'
import { cx } from '../../lib/cx'
import { AnalysisError } from './AnalysisError'
import { AnalysisProgress } from './AnalysisProgress'
import { AnalysisResult } from './AnalysisResult'
import { OUTCOME_META } from './outcomes'
import type { AnalysisState } from './useTicketAnalysis'
import styles from './ResultPanel.module.css'

interface ResultPanelProps {
  state: AnalysisState
  onRetry?: () => void
  onReset?: () => void
}

function isEditing(element: Element | null): boolean {
  return (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement
  )
}

export function ResultPanel({ state, onRetry, onReset }: ResultPanelProps) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const settled = state.status === 'success' || state.status === 'error'

  // Bring keyboard and screen reader users to the outcome, unless they are typing.
  useEffect(() => {
    if (settled && !isEditing(document.activeElement)) bodyRef.current?.focus()
  }, [settled, state])

  return (
    <Card padding="lg" aria-labelledby="result-panel-title" className={styles.panel}>
      <CardHeader titleId="result-panel-title" title="Result" />

      <p className="visually-hidden" role="status">
        {state.status === 'loading' ? 'Analyzing ticket.' : null}
        {state.status === 'success' ? `Analysis complete: ${OUTCOME_META[state.response.outcome].label}.` : null}
      </p>

      <div ref={bodyRef} tabIndex={-1} className={styles.body} aria-busy={state.status === 'loading'}>
        {state.status === 'idle' ? (
          <>
            <EmptyState
              icon={<FileSearch aria-hidden="true" />}
              title="No ticket analyzed yet"
              description="Submit a ticket to see the agent’s outcome, recommendations, and sources here."
            />
            <OutcomeGuide />
          </>
        ) : null}

        {state.status === 'loading' ? <AnalysisProgress ticketId={state.ticket.ticket_id} /> : null}

        {state.status === 'error' ? <AnalysisError error={state.error} onRetry={onRetry} /> : null}
        {state.status === 'success' ? (
          <AnalysisResult response={state.response} ticket={state.ticket} onReset={onReset} />
        ) : null}
      </div>
    </Card>
  )
}

function OutcomeGuide() {
  return (
    <div className={styles.outcomes}>
      <h3 className={styles.outcomesTitle}>Possible outcomes</h3>
      <ul className={styles.outcomeList}>
        {Object.values(OUTCOME_META).map(({ label, tone, icon: Icon, description }) => (
          <li key={label} className={cx(styles.outcome, styles[tone])}>
            <Icon className={styles.outcomeIcon} aria-hidden="true" />
            <p className={styles.outcomeName}>{label}</p>
            <p className={styles.outcomeText}>{description}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}
