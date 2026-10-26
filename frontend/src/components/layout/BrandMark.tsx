import { cx } from '../../lib/cx'
import styles from './BrandMark.module.css'

interface BrandMarkProps {
  /** Mark only; the name stays available to assistive technology. */
  compact?: boolean
}

/** Lines of documentation resolving to a point: an answer grounded in the docs. */
export function BrandMark({ compact = false }: BrandMarkProps) {
  return (
    <span className={styles.brand}>
      <svg className={styles.mark} viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="8" className={styles.tile} />
        <path d="M9 11h14M9 16h10M9 21h6" className={styles.lines} />
        <circle cx="21" cy="21" r="2.2" className={styles.dot} />
      </svg>
      <span className={cx(styles.wordmark, compact && 'visually-hidden')}>
        <span className={styles.name}>Support Console</span>
        <span className={styles.descriptor}>Agentic RAG</span>
      </span>
    </span>
  )
}
