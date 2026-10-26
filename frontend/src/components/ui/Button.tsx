import type { ComponentPropsWithRef, ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Button.module.css'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ComponentPropsWithRef<'button'> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a spinner, disables the button, and sets aria-busy. */
  loading?: boolean
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
  fullWidth?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  leadingIcon,
  trailingIcon,
  fullWidth = false,
  type = 'button',
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(styles.button, styles[variant], styles[size], fullWidth && styles.fullWidth, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner size="sm" /> : leadingIcon}
      <span className={styles.label}>{children}</span>
      {trailingIcon}
    </button>
  )
}

export interface IconButtonProps extends Omit<ComponentPropsWithRef<'button'>, 'children'> {
  /** Accessible name; icon-only buttons must have one. */
  label: string
  icon: ReactNode
  variant?: Exclude<ButtonVariant, 'danger'>
  size?: ButtonSize
}

export function IconButton({
  label,
  icon,
  variant = 'ghost',
  size = 'md',
  type = 'button',
  className,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      className={cx(styles.button, styles.iconOnly, styles[variant], styles[size], className)}
      {...rest}
    >
      {icon}
    </button>
  )
}
