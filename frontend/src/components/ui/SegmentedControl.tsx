import { useId, type ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './SegmentedControl.module.css'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
  /** Show only the icon; the label stays available to assistive technology. */
  iconOnly?: boolean
}

interface SegmentedControlProps<T extends string> {
  legend: string
  options: ReadonlyArray<SegmentedOption<T>>
  value: T
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  className?: string
}

/** Radio group styled as a segmented control; arrow keys move between options natively. */
export function SegmentedControl<T extends string>({
  legend,
  options,
  value,
  onChange,
  size = 'md',
  className,
}: SegmentedControlProps<T>) {
  const name = useId()
  return (
    <fieldset className={cx(styles.group, styles[size], className)}>
      <legend className="visually-hidden">{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className={styles.option} title={option.iconOnly ? option.label : undefined}>
          <input
            type="radio"
            className={styles.input}
            name={name}
            value={option.value}
            checked={option.value === value}
            onChange={() => onChange(option.value)}
          />
          <span className={styles.face}>
            {option.icon}
            <span className={option.iconOnly ? 'visually-hidden' : undefined}>{option.label}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}
