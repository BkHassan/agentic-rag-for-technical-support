import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Sidebar.module.css'

interface NavItemProps {
  href: string
  label: string
  icon: ReactNode
  current?: boolean
  /** Rail mode: icon only, label kept for assistive technology and as a tooltip. */
  compact?: boolean
  onNavigate?: () => void
}

export function NavItem({ href, label, icon, current = false, compact = false, onNavigate }: NavItemProps) {
  return (
    <a
      href={href}
      className={cx(styles.navItem, current && styles.navItemCurrent, compact && styles.navItemCompact)}
      aria-current={current ? 'page' : undefined}
      title={compact ? label : undefined}
      onClick={onNavigate}
    >
      <span className={styles.navIcon}>{icon}</span>
      <span className={compact ? 'visually-hidden' : styles.navLabel}>{label}</span>
    </a>
  )
}
