import { useState } from 'react'
import { PageHeader } from '../../components/layout/PageHeader'
import { ResultPanel } from './ResultPanel'
import { TicketForm } from './TicketForm'
import { useTicketAnalysis } from './useTicketAnalysis'
import styles from './AnalyzePage.module.css'

export function AnalyzePage() {
  const { state, submit, reset } = useTicketAnalysis()
  // Remounting the form gives a fresh ticket ID and empty fields.
  const [formKey, setFormKey] = useState(0)

  function analyzeAnother() {
    reset()
    setFormKey((key) => key + 1)
  }

  return (
    <section id="analyze" aria-labelledby="analyze-title" className={styles.page}>
      <PageHeader
        titleId="analyze-title"
        title="Analyze a support ticket"
        description="The agent recommends troubleshooting steps only when the internal documentation supports them. Otherwise it asks for details or routes the ticket to manual review."
      />
      <div className={styles.grid}>
        <div className={styles.formColumn}>
          <TicketForm
            key={formKey}
            autoFocusDescription={formKey > 0}
            onSubmit={(ticket) => void submit(ticket)}
            submitting={state.status === 'loading'}
          />
        </div>
        <ResultPanel
          state={state}
          onRetry={state.status === 'error' ? () => void submit(state.ticket) : undefined}
          onReset={analyzeAnother}
        />
      </div>
    </section>
  )
}
