import { ChevronRight } from 'lucide-react'
import type { AgentResponse } from '../../../api/types'
import { EvidenceList } from './EvidenceList'
import { OutcomeHeader } from './OutcomeHeader'
import { SectionHeader } from './SectionHeader'
import styles from './results.module.css'

export function FallbackView({ response }: { response: AgentResponse }) {
  // The backend sends its fallback notice as `summary` and repeats it as the only recommendation.
  const message = response.summary ?? response.recommendations[0] ?? null
  const extraNotes = response.recommendations.filter((item) => item !== message)
  const rejectedCount = response.sources.length

  return (
    <>
      <OutcomeHeader outcome="fallback" title="No reliable documented answer">
        The available documentation does not contain enough relevant evidence, so the agent gives no troubleshooting
        steps. Review this ticket manually.
      </OutcomeHeader>

      <section className={styles.section}>
        <SectionHeader title="Message from the agent" />
        {message ? (
          <blockquote className={styles.message}>{message}</blockquote>
        ) : (
          <p className={styles.muted}>The backend did not include a message.</p>
        )}
      </section>

      {extraNotes.length > 0 ? (
        <section className={styles.section}>
          <SectionHeader title="Additional notes from the agent" />
          <ul className={styles.notes}>
            {extraNotes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {rejectedCount > 0 ? (
        <details className={styles.rejected}>
          <summary className={styles.rejectedSummary}>
            <ChevronRight className={styles.chevron} aria-hidden="true" />
            Show the {rejectedCount} {rejectedCount === 1 ? 'passage' : 'passages'} that were checked and rejected
          </summary>
          <p className={styles.note}>
            These passages did not meet the evidence requirements. They do not support any solution for this ticket.
          </p>
          <EvidenceList sources={response.sources} openCount={0} />
        </details>
      ) : null}
    </>
  )
}
