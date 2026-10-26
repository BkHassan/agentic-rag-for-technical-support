import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Card.module.css'

interface CardProps {
  as?: 'section' | 'article' | 'div'
  padding?: 'none' | 'sm' | 'md' | 'lg'
  elevated?: boolean
  className?: string
  children: ReactNode
  'aria-labelledby'?: string
}

export function Card({
  as: Element = 'section',
  padding = 'md',
  elevated = false,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <Element className={cx(styles.card, styles[`pad-${padding}`], elevated && styles.elevated, className)} {...rest}>
      {children}
    </Element>
  )
}

interface CardHeaderProps {
  title: ReactNode
  /** Heading level within the page outline. */
  level?: 2 | 3 | 4
  titleId?: string
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

export function CardHeader({ title, level = 2, titleId, description, actions, className }: CardHeaderProps) {
  const Heading = `h${level}` as const
  return (
    <header className={cx(styles.header, className)}>
      <div className={styles.headerText}>
        <Heading id={titleId} className={styles.title}>
          {title}
        </Heading>
        {description ? <p className={styles.description}>{description}</p> : null}
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  )
}
