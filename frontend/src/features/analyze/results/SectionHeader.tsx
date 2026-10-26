import { BookOpen, PenLine } from 'lucide-react'
import styles from './results.module.css'

const PROVENANCE = {
  generated: { icon: PenLine, text: 'Written by the agent' },
  retrieved: { icon: BookOpen, text: 'Excerpts from the documentation' },
} as const

interface SectionHeaderProps {
  title: string
  id?: string
  /** Says where the content comes from, so generated text and quoted sources are never confused. */
  provenance?: keyof typeof PROVENANCE
}

export function SectionHeader({ title, id, provenance }: SectionHeaderProps) {
  const tag = provenance ? PROVENANCE[provenance] : null
  return (
    <div className={styles.sectionHeader}>
      <h4 id={id} className={styles.sectionTitle}>
        {title}
      </h4>
      {tag ? (
        <span className={styles.provenance}>
          <tag.icon aria-hidden="true" />
          {tag.text}
        </span>
      ) : null}
    </div>
  )
}
