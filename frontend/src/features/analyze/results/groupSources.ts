import type { DocumentSource } from '../../../api/types'

export interface SourceDocument {
  /** Document filename as returned by the backend. */
  source: string
  category: string | null
  passages: DocumentSource[]
  /** Smallest cosine distance among the passages; null when the backend sent none. */
  bestDistance: number | null
}

/** Groups passages by document filename, keeping the backend's order of first appearance. */
export function groupSourcesByDocument(sources: ReadonlyArray<DocumentSource>): SourceDocument[] {
  const documents = new Map<string, SourceDocument>()
  for (const passage of sources) {
    const name = passage.source.trim() || 'Unnamed document'
    let document = documents.get(name)
    if (!document) {
      document = { source: name, category: passage.category, passages: [], bestDistance: null }
      documents.set(name, document)
    }
    document.passages.push(passage)
    document.category ??= passage.category
    if (passage.distance !== null && (document.bestDistance === null || passage.distance < document.bestDistance)) {
      document.bestDistance = passage.distance
    }
  }
  return [...documents.values()]
}
