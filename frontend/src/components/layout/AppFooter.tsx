import { ExternalLink } from 'lucide-react'
import styles from './AppFooter.module.css'

interface AppFooterProps {
  docsUrl: string
}

export function AppFooter({ docsUrl }: AppFooterProps) {
  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <p className={styles.note}>
          Recommendations come from the internal documentation set. Review them before applying changes.
        </p>
        <a className={styles.link} href={docsUrl} target="_blank" rel="noreferrer">
          API documentation
          <ExternalLink aria-hidden="true" />
          <span className="visually-hidden">(opens in a new tab)</span>
        </a>
      </div>
    </footer>
  )
}
