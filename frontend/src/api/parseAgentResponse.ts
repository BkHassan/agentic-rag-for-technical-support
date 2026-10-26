import { ApiError } from './errors'
import {
  AGENT_OUTCOMES,
  TICKET_CATEGORIES,
  type AgentOutcome,
  type AgentResponse,
  type DocumentSource,
  type TicketCategory,
} from './types'

class SchemaMismatch extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') throw new SchemaMismatch(key)
  return value
}

function requireBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key]
  if (typeof value !== 'boolean') throw new SchemaMismatch(key)
  return value
}

function optionalString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key]
  if (value === undefined || value === null) return null
  if (typeof value !== 'string') throw new SchemaMismatch(key)
  return value
}

function optionalNumber(record: Record<string, unknown>, key: string): number | null {
  const value = record[key]
  if (value === undefined || value === null) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new SchemaMismatch(key)
  return value
}

/** Lists with a server-side default may be omitted in the schema; absent means empty. */
function stringList(record: Record<string, unknown>, key: string): string[] {
  const value = record[key]
  if (value === undefined) return []
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) throw new SchemaMismatch(key)
  return value
}

function oneOf<T extends string>(record: Record<string, unknown>, key: string, allowed: ReadonlyArray<T>): T {
  const value = record[key]
  const match = allowed.find((item) => item === value)
  if (match === undefined) throw new SchemaMismatch(key)
  return match
}

function parseSource(value: unknown): DocumentSource {
  if (!isRecord(value)) throw new SchemaMismatch('sources')
  return {
    source: requireString(value, 'source'),
    doc_id: optionalString(value, 'doc_id'),
    chunk_index: optionalNumber(value, 'chunk_index'),
    category: optionalString(value, 'category'),
    distance: optionalNumber(value, 'distance'),
    excerpt: optionalString(value, 'excerpt'),
  }
}

/** Validates a 2xx body against the AgentResponse schema; throws ApiError('invalid_response'). */
export function parseAgentResponse(body: unknown): AgentResponse {
  try {
    if (!isRecord(body)) throw new SchemaMismatch('body')
    const sources = body.sources
    if (sources !== undefined && !Array.isArray(sources)) throw new SchemaMismatch('sources')
    return {
      ticket_id: requireString(body, 'ticket_id'),
      category: oneOf<TicketCategory>(body, 'category', TICKET_CATEGORIES),
      outcome: oneOf<AgentOutcome>(body, 'outcome', AGENT_OUTCOMES),
      clarifying_questions: stringList(body, 'clarifying_questions'),
      recommendations: stringList(body, 'recommendations'),
      sources: (sources ?? []).map(parseSource),
      sufficient_evidence: requireBoolean(body, 'sufficient_evidence'),
      fallback_triggered: requireBoolean(body, 'fallback_triggered'),
      summary: optionalString(body, 'summary'),
    }
  } catch (error) {
    if (!(error instanceof SchemaMismatch)) throw error
    throw new ApiError({
      kind: 'invalid_response',
      message: 'The backend returned a response this console could not read.',
    })
  }
}
