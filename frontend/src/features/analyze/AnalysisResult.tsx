import { FilePlus } from 'lucide-react'
import type { AgentResponse, SupportTicket } from '../../api/types'
import { Badge, Button } from '../../components/ui'
import { ClarificationView } from './results/ClarificationView'
import { FallbackView } from './results/FallbackView'
import { GroundedAnswerView } from './results/GroundedAnswerView'
import styles from './results/results.module.css'

interface AnalysisResultProps {
  response: AgentResponse
  /** The ticket as submitted; shown for clarification so the reporter's text stays visible. */
  ticket?: SupportTicket
  onReset?: () => void
}

export function AnalysisResult({ response, ticket, onReset }: AnalysisResultProps) {
  return (
    <div className={styles.result}>
      <div className={styles.meta}>
        <Badge mono>
          <span className="visually-hidden">Category: </span>
          {response.category}
        </Badge>
        <span className={styles.metaTicketId}>
          <span className="visually-hidden">Ticket ID: </span>
          {response.ticket_id}
        </span>
      </div>

      {response.outcome === 'grounded_answer' ? <GroundedAnswerView response={response} /> : null}
      {response.outcome === 'clarification_needed' ? <ClarificationView response={response} ticket={ticket} /> : null}
      {response.outcome === 'fallback' ? <FallbackView response={response} /> : null}

      <dl className={styles.flags}>
        <div>
          <dt>Evidence</dt>
          <dd>{response.sufficient_evidence ? 'Sufficient' : 'Insufficient'}</dd>
        </div>
        <div>
          <dt>Fallback triggered</dt>
          <dd>{response.fallback_triggered ? 'Yes' : 'No'}</dd>
        </div>
      </dl>

      {onReset ? (
        <div className={styles.actions}>
          <Button leadingIcon={<FilePlus aria-hidden="true" />} onClick={onReset}>
            Analyze another ticket
          </Button>
        </div>
      ) : null}
    </div>
  )
}
