import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { RefreshCw, Send } from 'lucide-react'
import type { SupportTicket } from '../../api/types'
import { Button, Card, CardHeader, Field, IconButton, TextInput, Textarea } from '../../components/ui'
import { ExamplePicker } from './ExamplePicker'
import type { ExampleTicket } from './exampleTickets'
import { generateTicketId } from './ticketId'
import styles from './TicketForm.module.css'

interface FieldErrors {
  ticketId?: string
  description?: string
}

interface TicketFormProps {
  onSubmit: (ticket: SupportTicket) => void
  /** Called by "Clear" so a result or error for the cleared text does not stay on screen. */
  onClear?: () => void
  /** While true the form keeps its values but cannot be edited or submitted again. */
  submitting?: boolean
  /** Focus the description on mount, e.g. after "Analyze another ticket". */
  autoFocusDescription?: boolean
}

export function TicketForm({ onSubmit, onClear, submitting = false, autoFocusDescription = false }: TicketFormProps) {
  const [ticketId, setTicketId] = useState(() => generateTicketId())
  const [description, setDescription] = useState('')
  const [exampleId, setExampleId] = useState('')
  const [savedDraft, setSavedDraft] = useState<string | null>(null)
  const [errors, setErrors] = useState<FieldErrors>({})
  const ticketIdRef = useRef<HTMLInputElement>(null)
  const descriptionRef = useRef<HTMLTextAreaElement>(null)

  const loadedExample = exampleId !== ''

  useEffect(() => {
    if (autoFocusDescription) descriptionRef.current?.focus()
  }, [autoFocusDescription])

  function loadExample(example: ExampleTicket) {
    if (!loadedExample && description.trim()) setSavedDraft(description)
    setDescription(example.description)
    setExampleId(example.id)
    setErrors((current) => ({ ...current, description: undefined }))
  }

  function restoreDraft() {
    setDescription(savedDraft ?? '')
    setSavedDraft(null)
    setExampleId('')
    descriptionRef.current?.focus()
  }

  function clearForm() {
    setDescription('')
    setExampleId('')
    setSavedDraft(null)
    setErrors({})
    onClear?.()
    descriptionRef.current?.focus()
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    // Mirrors the backend: values are stripped and must not be blank.
    const ticket: SupportTicket = { ticket_id: ticketId.trim(), description: description.trim() }
    const nextErrors: FieldErrors = {
      ticketId: ticket.ticket_id ? undefined : 'Enter a ticket ID.',
      description: ticket.description ? undefined : 'Describe the issue before analyzing.',
    }
    setErrors(nextErrors)
    if (nextErrors.ticketId) return ticketIdRef.current?.focus()
    if (nextErrors.description) return descriptionRef.current?.focus()
    onSubmit(ticket)
  }

  function handleDescriptionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  const characterCount = description.trim().length

  return (
    <Card padding="lg" aria-labelledby="ticket-form-title">
      <CardHeader
        titleId="ticket-form-title"
        title="Ticket"
        description="Describe what fails and what you observe. Specific symptoms, error messages, and codes lead to documented steps."
      />

      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <ExamplePicker value={exampleId} onSelect={loadExample} disabled={submitting} />

        {loadedExample && savedDraft !== null ? (
          <p className={styles.draftNotice} role="status">
            The example replaced your text.
            <Button variant="ghost" size="sm" onClick={restoreDraft}>
              Restore your text
            </Button>
          </p>
        ) : null}

        <Field
          label="Ticket ID"
          hint="Auto-generated. Replace it if the ticket already has an ID."
          error={errors.ticketId}
          required
        >
          {(control) => (
            <div className={styles.idRow}>
              <TextInput
                {...control}
                ref={ticketIdRef}
                mono
                autoComplete="off"
                spellCheck={false}
                value={ticketId}
                readOnly={submitting}
                onChange={(event) => {
                  setTicketId(event.target.value)
                  if (errors.ticketId) setErrors((current) => ({ ...current, ticketId: undefined }))
                }}
              />
              <IconButton
                variant="secondary"
                label="Generate a new ticket ID"
                icon={<RefreshCw aria-hidden="true" />}
                title="Generate a new ticket ID"
                disabled={submitting}
                onClick={() => {
                  setTicketId(generateTicketId())
                  setErrors((current) => ({ ...current, ticketId: undefined }))
                }}
              />
            </div>
          )}
        </Field>

        <Field
          label="Issue description"
          hint={
            <span className={styles.shortcut}>
              Press <kbd className={styles.kbd}>Ctrl</kbd>
              <span aria-hidden="true">+</span>
              <kbd className={styles.kbd}>Enter</kbd> to analyze.
            </span>
          }
          error={errors.description}
          required
          meta={characterCount > 0 ? `${characterCount} characters` : undefined}
        >
          {(control) => (
            <Textarea
              {...control}
              ref={descriptionRef}
              rows={6}
              placeholder="For example: the orders service logs “connection timed out” when connecting to PostgreSQL since 09:00."
              value={description}
              readOnly={submitting}
              onKeyDown={handleDescriptionKeyDown}
              onChange={(event) => {
                setDescription(event.target.value)
                if (loadedExample) setExampleId('')
                if (errors.description) setErrors((current) => ({ ...current, description: undefined }))
              }}
            />
          )}
        </Field>

        <div className={styles.actions}>
          <div className={styles.buttons}>
            <Button variant="ghost" onClick={clearForm} disabled={submitting || (!description && savedDraft === null)}>
              Clear
            </Button>
            <Button type="submit" variant="primary" loading={submitting} leadingIcon={<Send aria-hidden="true" />}>
              {submitting ? 'Analyzing…' : 'Analyze ticket'}
            </Button>
          </div>
        </div>
      </form>
    </Card>
  )
}
