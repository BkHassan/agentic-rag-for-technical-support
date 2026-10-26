import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { BrandMark } from './BrandMark'
import { NavItem } from './NavItem'
import styles from './Sidebar.module.css'

export interface NavigationItem {
  id: string
  label: string
  href: string
  icon: ReactNode
}

interface SidebarProps {
  id?: string
  items: ReadonlyArray<NavigationItem>
  currentId: string
  collapsed?: boolean
  /** Rendered at the bottom, e.g. system status. Receives the collapsed state. */
  footer?: (collapsed: boolean) => ReactNode
  onNavigate?: () => void
}

export function Sidebar({ id, items, currentId, collapsed = false, footer, onNavigate }: SidebarProps) {
  return (
    <div id={id} className={cx(styles.sidebar, collapsed && styles.collapsed)}>
      <div className={styles.brandRow}>
        <a href="#analyze" className={styles.brandLink} onClick={onNavigate}>
          <BrandMark compact={collapsed} />
        </a>
      </div>
      <nav className={styles.nav} aria-label="Primary">
        <ul className={styles.navList}>
          {items.map((item) => (
            <li key={item.id}>
              <NavItem
                href={item.href}
                label={item.label}
                icon={item.icon}
                current={item.id === currentId}
                compact={collapsed}
                onNavigate={onNavigate}
              />
            </li>
          ))}
        </ul>
      </nav>
      {footer ? <div className={styles.footer}>{footer(collapsed)}</div> : null}
    </div>
  )
}
