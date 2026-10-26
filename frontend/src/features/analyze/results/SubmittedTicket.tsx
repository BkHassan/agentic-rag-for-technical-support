import type { SupportTicket } from '../../../api/types'
import { SectionHeader } from './SectionHeader'
import styles from './results.module.css'

export function SubmittedTicket({ ticket }: { ticket: SupportTicket }) {
  return (
    <section className={styles.section} aria-labelledby="submitted-ticket-title">
      <SectionHeader id="submitted-ticket-title" title="Your ticket" />
      <figure className={styles.ticket}>
        <figcaption className={styles.ticketId}>{ticket.ticket_id}</figcaption>
        <blockquote className={styles.ticketText}>{ticket.description}</blockquote>
      </figure>
    </section>
  )
}
