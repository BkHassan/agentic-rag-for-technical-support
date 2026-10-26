import { cx } from '../../lib/cx'
import type { ApiHealth } from './useApiHealth'
import styles from './status.module.css'

const LABELS = {
  checking: 'Checking API',
  online: 'API online',
  offline: 'API offline',
} as const

/** Compact top-bar indicator. Reflects only the latest real GET /health result. */
export function ConnectionIndicator({ health }: { health: ApiHealth }) {
  const title =
    health.status === 'offline' && health.reason
      ? health.reason
      : health.checkedAt
        ? `Last checked ${health.checkedAt.toLocaleTimeString()}`
        : undefined

  return (
    <span className={cx(styles.indicator, styles[health.status])} title={title}>
      <span className={styles.dot} aria-hidden="true" />
      <span className={styles.indicatorLabel}>{LABELS[health.status]}</span>
    </span>
  )
}
