import { useId, type ComponentPropsWithRef, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { cx } from '../../lib/cx'
import styles from './Field.module.css'

export interface FieldControlProps {
  id: string
  'aria-describedby'?: string
  'aria-invalid'?: true
  required?: boolean
}

interface FieldProps {
  label: ReactNode
  hint?: ReactNode
  /** Shown below the control and linked via aria-describedby; marks the control invalid. */
  error?: ReactNode
  required?: boolean
  /** Right-aligned next to the label, e.g. a character count. */
  meta?: ReactNode
  className?: string
  children: (control: FieldControlProps) => ReactNode
}

export function Field({ label, hint, error, required = false, meta, className, children }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className={cx(styles.field, className)}>
      <div className={styles.labelRow}>
        <label htmlFor={id} className={styles.label}>
          {label}
          {required ? (
            <span className={styles.required} aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
        {meta ? <span className={styles.meta}>{meta}</span> : null}
      </div>
      {children({
        id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
        required,
      })}
      {hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className={styles.error}>
          {error}
        </p>
      ) : null}
    </div>
  )
}

interface TextInputProps extends ComponentPropsWithRef<'input'> {
  mono?: boolean
}

export function TextInput({ mono = false, className, type = 'text', ...rest }: TextInputProps) {
  return <input type={type} className={cx(styles.control, mono && styles.mono, className)} {...rest} />
}

export function Textarea({ className, rows = 5, ...rest }: ComponentPropsWithRef<'textarea'>) {
  return <textarea rows={rows} className={cx(styles.control, styles.textarea, className)} {...rest} />
}

export function Select({ className, children, ...rest }: ComponentPropsWithRef<'select'>) {
  return (
    <span className={cx(styles.selectWrap, className)}>
      <select className={cx(styles.control, styles.select)} {...rest}>
        {children}
      </select>
      <ChevronDown className={styles.selectIcon} aria-hidden="true" />
    </span>
  )
}
