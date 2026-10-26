import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { IconButton } from '../ui'
import styles from './MobileNavDrawer.module.css'

interface MobileNavDrawerProps {
  id: string
  open: boolean
  onClose: () => void
  children: ReactNode
}

/**
 * Off-canvas navigation for small screens. A modal <dialog> provides the focus
 * trap, Escape to close, inert background, and focus return to the opener.
 */
export function MobileNavDrawer({ id, open, onClose, children }: MobileNavDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      id={id}
      ref={dialogRef}
      className={styles.drawer}
      aria-label="Navigation"
      onClose={onClose}
      onClick={(event) => {
        // The panel fills the dialog, so a click on the dialog itself is on the backdrop.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className={styles.panel}>
        <IconButton
          className={styles.close}
          label="Close navigation"
          icon={<X aria-hidden="true" />}
          size="sm"
          onClick={onClose}
        />
        {children}
      </div>
    </dialog>
  )
}
