import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './EmptyState.module.css'

interface EmptyStateProps {
  icon?: ReactNode
  title: ReactNode
  /** Heading level within the page outline. */
  level?: 2 | 3 | 4
  description?: ReactNode
  className?: string
  children?: ReactNode
}

export function EmptyState({ icon, title, level = 3, description, className, children }: EmptyStateProps) {
  const Heading = `h${level}` as const
  return (
    <div className={cx(styles.emptyState, className)}>
      {icon ? <span className={styles.icon}>{icon}</span> : null}
      <Heading className={styles.title}>{title}</Heading>
      {description ? <p className={styles.description}>{description}</p> : null}
      {children}
    </div>
  )
}
