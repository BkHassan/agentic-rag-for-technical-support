import { RotateCcw } from 'lucide-react'
import type { ApiError, ApiErrorKind, TicketField } from '../../api/errors'
import { Button, Callout } from '../../components/ui'
import styles from './ResultPanel.module.css'

const TITLES: Record<ApiErrorKind, string> = {
  validation: 'Ticket rejected',
  bad_request: 'Request not processed',
  rate_limit: 'Rate limit reached',
  upstream: 'Model service error',
  unavailable: 'Service unavailable',
  server: 'Backend error',
  http: 'Request failed',
  network: 'Backend unreachable',
  timeout: 'Request timed out',
  invalid_response: 'Unexpected response',
  unknown: 'Request failed',
}

const FIELD_LABELS: Record<TicketField, string> = {
  ticket_id: 'Ticket ID',
  description: 'Issue description',
}

function formatWait(seconds: number): string {
  if (seconds < 90) return `${seconds} seconds`
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return `${minutes} minutes`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}

interface AnalysisErrorProps {
  error: ApiError
  onRetry?: () => void
}

export function AnalysisError({ error, onRetry }: AnalysisErrorProps) {
  const fieldErrors = Object.entries(error.fieldErrors) as Array<[TicketField, string]>
  // Resending an invalid body would fail the same way; the user has to edit it first.
  const canRetry = onRetry && error.kind !== 'validation'

  return (
    <Callout
      tone="danger"
      role="alert"
      title={TITLES[error.kind]}
      actions={
        canRetry ? (
          <Button size="sm" leadingIcon={<RotateCcw aria-hidden="true" />} onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      <p>{error.message}</p>
      {error.retryAfterSeconds ? <p>Gemini suggests retrying in about {formatWait(error.retryAfterSeconds)}.</p> : null}
      {fieldErrors.length > 0 ? (
        <ul className={styles.fieldErrors}>
          {fieldErrors.map(([field, message]) => (
            <li key={field}>
              {FIELD_LABELS[field]}: {message}
            </li>
          ))}
        </ul>
      ) : null}
      <p className={styles.errorFootnote}>
        Your ticket is still in the form.
        {error.status ? <span className={styles.statusCode}> HTTP {error.status}</span> : null}
      </p>
    </Callout>
  )
}
