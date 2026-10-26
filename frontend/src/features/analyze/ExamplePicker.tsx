import { useId } from 'react'
import { Select } from '../../components/ui'
import { EXAMPLE_TICKETS, type ExampleTicket } from './exampleTickets'
import styles from './TicketForm.module.css'

interface ExamplePickerProps {
  /** Id of the loaded example, or "" when the description is the user's own text. */
  value: string
  onSelect: (example: ExampleTicket) => void
  disabled?: boolean
}

export function ExamplePicker({ value, onSelect, disabled = false }: ExamplePickerProps) {
  const id = useId()
  const hintId = `${id}-hint`

  return (
    <div className={styles.examples}>
      <div className={styles.examplesText}>
        <label htmlFor={id} className={styles.examplesLabel}>
          Start from an example
        </label>
        <span id={hintId} className={styles.examplesHint}>
          Written for this demo, not real tickets
        </span>
      </div>
      <Select
        id={id}
        className={styles.examplesSelect}
        aria-describedby={hintId}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          const example = EXAMPLE_TICKETS.find((item) => item.id === event.target.value)
          if (example) onSelect(example)
        }}
      >
        <option value="" disabled>
          Choose an example…
        </option>
        {EXAMPLE_TICKETS.map((example) => (
          <option key={example.id} value={example.id}>
            {example.label}
          </option>
        ))}
      </Select>
    </div>
  )
}
