import type { AgentResponse, SupportTicket } from '../../../api/types'
import { OutcomeHeader } from './OutcomeHeader'
import { SectionHeader } from './SectionHeader'
import { SubmittedTicket } from './SubmittedTicket'
import styles from './results.module.css'

interface ClarificationViewProps {
  response: AgentResponse
  ticket?: SupportTicket
}

export function ClarificationView({ response, ticket }: ClarificationViewProps) {
  return (
    <>
      <OutcomeHeader outcome="clarification_needed" title="More information needed">
        The ticket does not describe the problem specifically enough for a reliable recommendation, so no
        troubleshooting steps were generated.
      </OutcomeHeader>

      {response.summary ? (
        <section className={styles.section}>
          <SectionHeader title="Message from the agent" />
          <p className={styles.prose}>{response.summary}</p>
        </section>
      ) : null}

      <section className={styles.section}>
        <SectionHeader title="Questions from the agent" />
        {response.clarifying_questions.length > 0 ? (
          <ol className={styles.steps}>
            {response.clarifying_questions.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ol>
        ) : (
          <p className={styles.muted}>The backend returned no specific questions.</p>
        )}
      </section>

      {ticket ? <SubmittedTicket ticket={ticket} /> : null}

      <p className={styles.note}>Add the missing details to the description in the form, then analyze it again.</p>
    </>
  )
}
