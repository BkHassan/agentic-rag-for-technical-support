import { cx } from '../../lib/cx'
import styles from './Spinner.module.css'

interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  /** When set, the spinner is announced as a status; otherwise it is decorative. */
  label?: string
  className?: string
}

export function Spinner({ size = 'md', label, className }: SpinnerProps) {
  const svg = (
    <svg className={cx(styles.spinner, styles[size], className)} viewBox="0 0 24 24" aria-hidden="true">
      <circle className={styles.track} cx="12" cy="12" r="9" />
      <path className={styles.arc} d="M21 12a9 9 0 0 0-9-9" />
    </svg>
  )
  if (!label) return svg
  return (
    <span role="status" className={styles.labelled}>
      {svg}
      <span className="visually-hidden">{label}</span>
    </span>
  )
}
