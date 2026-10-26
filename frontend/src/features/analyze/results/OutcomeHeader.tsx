import type { ReactNode } from 'react'
import type { AgentOutcome } from '../../../api/types'
import { cx } from '../../../lib/cx'
import { OUTCOME_META } from '../outcomes'
import styles from './results.module.css'

interface OutcomeHeaderProps {
  outcome: AgentOutcome
  title: string
  children?: ReactNode
}

/** The outcome name is always shown as text, so the state never depends on color or icon alone. */
export function OutcomeHeader({ outcome, title, children }: OutcomeHeaderProps) {
  const { label, tone, icon: Icon } = OUTCOME_META[outcome]
  return (
    <div className={cx(styles.outcomeHeader, styles[tone])}>
      <span className={styles.outcomeIcon}>
        <Icon aria-hidden="true" />
      </span>
      <div className={styles.outcomeText}>
        <p className={styles.outcomeLabel}>{label}</p>
        <h3 className={styles.outcomeTitle}>{title}</h3>
        {children ? <div className={styles.outcomeDescription}>{children}</div> : null}
      </div>
    </div>
  )
}
