import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Badge.module.css'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'

interface BadgeProps {
  tone?: Tone
  icon?: ReactNode
  /** Monospace for identifiers such as ticket IDs, filenames, and enum values. */
  mono?: boolean
  className?: string
  children: ReactNode
}

export function Badge({ tone = 'neutral', icon, mono = false, className, children }: BadgeProps) {
  return (
    <span className={cx(styles.badge, styles[tone], mono && styles.mono, className)}>
      {icon}
      {children}
    </span>
  )
}
