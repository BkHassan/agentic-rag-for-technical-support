/**
 * Types for POST /tickets/analyze, matching the backend's Pydantic models
 * (app/schemas.py) and the OpenAPI schema FastAPI generates from them.
 */

export const TICKET_CATEGORIES = ['network', 'database', 'authentication', 'application', 'other'] as const
export type TicketCategory = (typeof TICKET_CATEGORIES)[number]

export const AGENT_OUTCOMES = ['grounded_answer', 'clarification_needed', 'fallback'] as const
export type AgentOutcome = (typeof AGENT_OUTCOMES)[number]

/**
 * Request body (`SupportTicket`). The backend strips whitespace and rejects
 * blank values with HTTP 422; there is no maximum length.
 */
export interface SupportTicket {
  ticket_id: string
  description: string
}

/** A retrieved documentation chunk (`DocumentSource`). Only `source` is required. */
export interface DocumentSource {
  /** Original document filename. */
  source: string
  doc_id: string | null
  chunk_index: number | null
  category: string | null
  /** Chroma cosine distance; lower is closer. */
  distance: number | null
  /** Preview of the chunk text, at most 240 characters. */
  excerpt: string | null
}

/**
 * Response body (`AgentResponse`).
 *
 * - grounded_answer: `recommendations` are documented steps and `sources` back them.
 * - clarification_needed: `clarifying_questions` is filled; `recommendations` and `sources` are empty.
 * - fallback: `recommendations` holds only the fallback notice, and `sources` lists the
 *   closest chunks that were checked and rejected as insufficient evidence.
 */
export interface AgentResponse {
  ticket_id: string
  category: TicketCategory
  outcome: AgentOutcome
  clarifying_questions: string[]
  recommendations: string[]
  sources: DocumentSource[]
  sufficient_evidence: boolean
  fallback_triggered: boolean
  summary: string | null
}
