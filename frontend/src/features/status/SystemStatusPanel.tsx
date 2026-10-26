import { useId } from 'react'
import { RefreshCw } from 'lucide-react'
import { IconButton } from '../../components/ui'
import { cx } from '../../lib/cx'
import type { ApiHealth } from './useApiHealth'
import styles from './status.module.css'

const STATUS_TEXT = {
  checking: 'Checking…',
  online: 'Online',
  offline: 'Offline',
} as const

interface SystemStatusPanelProps {
  health: ApiHealth
  /** Rail mode: dot only, details stay available to assistive technology. */
  compact?: boolean
}

export function SystemStatusPanel({ health, compact = false }: SystemStatusPanelProps) {
  const headingId = useId()

  if (compact) {
    return (
      <section className={styles.panelCompact} aria-labelledby={headingId}>
        <h2 id={headingId} className="visually-hidden">
          System status
        </h2>
        <span
          className={cx(styles.dot, styles.dotLarge, styles[health.status])}
          title={`API ${STATUS_TEXT[health.status].toLowerCase()}`}
        />
        <span className="visually-hidden">API {STATUS_TEXT[health.status]}</span>
      </section>
    )
  }

  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <div className={styles.panelHeader}>
        <h2 id={headingId} className={styles.panelTitle}>
          System status
        </h2>
        <IconButton
          label="Check API status again"
          icon={<RefreshCw aria-hidden="true" />}
          size="sm"
          onClick={health.recheck}
          disabled={health.status === 'checking'}
        />
      </div>
      <dl className={styles.rows}>
        <div className={styles.row}>
          <dt>API</dt>
          <dd className={cx(styles.value, styles[health.status])}>
            <span className={styles.dot} aria-hidden="true" />
            {STATUS_TEXT[health.status]}
          </dd>
        </div>
        {health.checkedAt ? (
          <div className={styles.row}>
            <dt>Last check</dt>
            <dd className={styles.time}>
              <time dateTime={health.checkedAt.toISOString()}>{health.checkedAt.toLocaleTimeString()}</time>
            </dd>
          </div>
        ) : null}
      </dl>
      {health.status === 'offline' && health.reason ? <p className={styles.reason}>{health.reason}</p> : null}
      <p className={styles.note}>Confirms the API responds. Gemini quota and the document index are not checked.</p>
    </section>
  )
}
