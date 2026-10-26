import type { AgentResponse } from '../../../api/types'
import { cx } from '../../../lib/cx'
import { EvidenceList } from './EvidenceList'
import { OutcomeHeader } from './OutcomeHeader'
import { SectionHeader } from './SectionHeader'
import styles from './results.module.css'

export function GroundedAnswerView({ response }: { response: AgentResponse }) {
  return (
    <>
      <OutcomeHeader outcome="grounded_answer" title="Documented troubleshooting steps">
        The agent wrote these steps from documentation passages that passed the relevance check.
      </OutcomeHeader>

      {response.summary ? (
        <section className={styles.section}>
          <SectionHeader title="Summary" provenance="generated" />
          <p className={styles.prose}>{response.summary}</p>
        </section>
      ) : null}

      <section className={styles.section}>
        <SectionHeader title="Recommended steps" provenance="generated" />
        {response.recommendations.length > 0 ? (
          <ol className={styles.steps}>
            {response.recommendations.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        ) : (
          <p className={styles.muted}>The backend returned no steps for this ticket.</p>
        )}
      </section>

      <section className={cx(styles.section, styles.sourcesPanel)}>
        <SectionHeader title="Evidence" provenance="retrieved" />
        {response.sources.length > 0 ? (
          <>
            <p className={styles.note}>
              Documentation passages returned with this answer. They support the answer as a whole; the backend does
              not link individual steps to specific passages.
            </p>
            <EvidenceList sources={response.sources} />
          </>
        ) : (
          <p className={styles.muted}>The backend returned no source passages for this answer.</p>
        )}
      </section>
    </>
  )
}
