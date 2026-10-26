import { useEffect, useState } from 'react'
import { ANALYZE_TIMEOUT_MS } from '../../api/tickets'
import { Spinner } from '../../components/ui'
import styles from './ResultPanel.module.css'

/** Matches the latency recorded during evaluation; past this the user is told it is slow. */
const SLOW_AFTER_SECONDS = 30

function useElapsedSeconds(): number {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return seconds
}

export function AnalysisProgress({ ticketId }: { ticketId: string }) {
  const elapsed = useElapsedSeconds()
  const slow = elapsed >= SLOW_AFTER_SECONDS

  return (
    <div className={styles.progress}>
      <div className={styles.progressHeader}>
        <Spinner size="md" className={styles.progressSpinner} />
        <div className={styles.progressText}>
          <p className={styles.progressLabel}>Analyzing</p>
          <p className={styles.progressTitle}>
            Ticket <span className={styles.mono}>{ticketId}</span>
          </p>
          <p className={styles.progressDescription}>
            The agent is classifying the ticket, retrieving documentation passages, and checking whether they support
            an answer. This usually takes 5 to 30 seconds.
          </p>
          <p className={styles.slowNotice} role="status">
            {slow
              ? `This is taking longer than usual. The request stops automatically after ${ANALYZE_TIMEOUT_MS / 1000} seconds.`
              : null}
          </p>
        </div>
        <p className={styles.elapsed}>
          <span className="visually-hidden">Elapsed time: </span>
          {elapsed} s
        </p>
      </div>

      <div className={styles.skeleton} aria-hidden="true">
        <span className={styles.skeletonLabel} />
        <span className={styles.skeletonLine} />
        <span className={styles.skeletonLine} />
        <span className={styles.skeletonLineShort} />
        <span className={styles.skeletonLabel} />
        <span className={styles.skeletonLine} />
        <span className={styles.skeletonLineShort} />
      </div>
    </div>
  )
}
