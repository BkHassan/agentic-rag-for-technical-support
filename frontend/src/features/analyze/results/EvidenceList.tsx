import { ChevronRight, FileText } from 'lucide-react'
import type { DocumentSource } from '../../../api/types'
import { Badge } from '../../../components/ui'
import { groupSourcesByDocument } from './groupSources'
import styles from './results.module.css'

interface EvidenceListProps {
  sources: ReadonlyArray<DocumentSource>
  /** Number of documents expanded initially. */
  openCount?: number
}

function passageLabel(passage: DocumentSource, index: number): string {
  return passage.chunk_index !== null ? `Passage ${passage.chunk_index + 1}` : `Passage ${index + 1}`
}

/** Retrieved passages grouped by document, each document in a disclosure. */
export function EvidenceList({ sources, openCount = 1 }: EvidenceListProps) {
  const documents = groupSourcesByDocument(sources)

  return (
    <ul className={styles.documents}>
      {documents.map((document, index) => (
        <li key={document.source}>
          <details className={styles.document} open={index < openCount}>
            <summary className={styles.documentSummary}>
              <ChevronRight className={styles.chevron} aria-hidden="true" />
              <FileText className={styles.documentIcon} aria-hidden="true" />
              <span className={styles.documentName}>{document.source}</span>
              <span className={styles.documentMeta}>
                {document.category ? <Badge>{document.category}</Badge> : null}
                <span>
                  {document.passages.length} {document.passages.length === 1 ? 'passage' : 'passages'}
                </span>
              </span>
            </summary>
            <ol className={styles.passages}>
              {document.passages.map((passage, passageIndex) => (
                <li key={`${passage.chunk_index ?? 'n'}-${passageIndex}`} className={styles.passage}>
                  <div className={styles.passageMeta}>
                    <span>{passageLabel(passage, passageIndex)}</span>
                    {passage.distance !== null ? (
                      <span title="Cosine distance between the ticket and this passage; lower is closer.">
                        distance {passage.distance.toFixed(3)}
                      </span>
                    ) : null}
                  </div>
                  {passage.excerpt ? (
                    <blockquote className={styles.excerpt}>{passage.excerpt}</blockquote>
                  ) : (
                    <p className={styles.muted}>No excerpt was returned for this passage.</p>
                  )}
                </li>
              ))}
            </ol>
          </details>
        </li>
      ))}
    </ul>
  )
}
