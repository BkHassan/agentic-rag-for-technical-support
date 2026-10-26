import type { ReactNode } from 'react'
import styles from './TopBar.module.css'

interface TopBarProps {
  title: string
  /** Sidebar toggle or menu button. */
  navigationControl: ReactNode
  actions?: ReactNode
}

export function TopBar({ title, navigationControl, actions }: TopBarProps) {
  return (
    <header className={styles.topBar}>
      <div className={styles.start}>
        {navigationControl}
        <span className={styles.divider} aria-hidden="true" />
        <p className={styles.title}>{title}</p>
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  )
}
