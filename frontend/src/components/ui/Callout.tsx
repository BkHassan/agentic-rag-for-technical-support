import type { ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, MessageCircleQuestionMark, TriangleAlert } from 'lucide-react'
import { cx } from '../../lib/cx'
import type { Tone } from './Badge'
import styles from './Callout.module.css'

const DEFAULT_ICONS: Record<Tone, ReactNode> = {
  neutral: <Info aria-hidden="true" />,
  accent: <MessageCircleQuestionMark aria-hidden="true" />,
  success: <CircleCheck aria-hidden="true" />,
  warning: <TriangleAlert aria-hidden="true" />,
  danger: <CircleAlert aria-hidden="true" />,
}

interface CalloutProps {
  tone?: Tone
  title?: ReactNode
  icon?: ReactNode
  /** Use "alert" only for errors that need immediate attention; "status" for polite updates. */
  role?: 'alert' | 'status'
  actions?: ReactNode
  className?: string
  children?: ReactNode
}

export function Callout({ tone = 'neutral', title, icon, role, actions, className, children }: CalloutProps) {
  return (
    <div role={role} className={cx(styles.callout, styles[tone], className)}>
      <span className={styles.icon}>{icon ?? DEFAULT_ICONS[tone]}</span>
      <div className={styles.content}>
        {title ? <p className={styles.title}>{title}</p> : null}
        {children ? <div className={styles.body}>{children}</div> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>
  )
}
