import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentResponse, DocumentSource } from '../../../api/types'
import {
  RECORDED_CLARIFICATION_NEEDED,
  RECORDED_FALLBACK,
  RECORDED_GROUNDED_ANSWER,
} from '../../../test/recordedResponses'
import { AnalysisResult } from '../AnalysisResult'
import { groupSourcesByDocument } from './groupSources'

const grounded = RECORDED_GROUNDED_ANSWER as AgentResponse
const clarification = RECORDED_CLARIFICATION_NEEDED as AgentResponse
const fallback = RECORDED_FALLBACK as AgentResponse

function source(overrides: Partial<DocumentSource> & Pick<DocumentSource, 'source'>): DocumentSource {
  return { doc_id: null, chunk_index: null, category: null, distance: null, excerpt: null, ...overrides }
}

/** Testing Library collapses whitespace in rendered text, not in the expected string. */
function collapsed(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/** The section (heading + content) introduced by a heading. */
function section(name: string): HTMLElement {
  const heading = screen.getByRole('heading', { name })
  const container = heading.closest('section')
  if (!container) throw new Error(`No section for heading "${name}"`)
  return container
}

afterEach(cleanup)

describe('grounded_answer', () => {
  it('separates the summary, the recommended steps, and the evidence', () => {
    render(<AnalysisResult response={grounded} />)

    expect(screen.getByText('Grounded answer')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Documented troubleshooting steps' })).toBeTruthy()
    expect(within(section('Summary')).getByText(grounded.summary as string)).toBeTruthy()

    const steps = within(section('Recommended steps')).getAllByRole('listitem')
    expect(steps.map((item) => item.textContent)).toEqual(grounded.recommendations)

    const evidence = section('Evidence')
    for (const passage of grounded.sources) {
      expect(within(evidence).getByText(passage.source)).toBeTruthy()
      expect(within(evidence).getByText(collapsed(passage.excerpt as string))).toBeTruthy()
    }
  })

  it('does not attach sources to individual steps', () => {
    render(<AnalysisResult response={grounded} />)

    const steps = section('Recommended steps')
    for (const passage of grounded.sources) {
      expect(within(steps).queryByText(new RegExp(passage.source.replace('.', '\\.')))).toBeNull()
    }
    expect(within(section('Evidence')).getByText(/does not link individual steps to specific passages/)).toBeTruthy()
  })

  it('opens the first document and keeps the rest collapsed', () => {
    const { container } = render(<AnalysisResult response={grounded} />)

    const documents = Array.from(container.querySelectorAll('details'))
    expect(documents).toHaveLength(2)
    expect(documents.map((item) => item.open)).toEqual([true, false])
  })

  it('handles a response without summary, steps, or sources', () => {
    render(<AnalysisResult response={{ ...grounded, summary: null, recommendations: [], sources: [] }} />)

    expect(screen.queryByRole('heading', { name: 'Summary' })).toBeNull()
    expect(screen.getByText('The backend returned no steps for this ticket.')).toBeTruthy()
    expect(screen.getByText('The backend returned no source passages for this answer.')).toBeTruthy()
  })

  it('groups passages by document and tolerates missing optional source fields', () => {
    const sources = [
      source({ source: 'vpn_split_tunnel.md', chunk_index: 2, excerpt: 'Check the split tunnel routes.', distance: 0.31 }),
      source({ source: 'vpn_split_tunnel.md' }),
    ]
    render(<AnalysisResult response={{ ...grounded, sources }} />)

    const evidence = section('Evidence')
    expect(within(evidence).getAllByText('vpn_split_tunnel.md')).toHaveLength(1)
    expect(within(evidence).getByText('2 passages')).toBeTruthy()
    expect(within(evidence).getByText('Passage 3')).toBeTruthy()
    expect(within(evidence).getByText('distance 0.310')).toBeTruthy()
    expect(within(evidence).getByText('No excerpt was returned for this passage.')).toBeTruthy()
  })
})

describe('clarification_needed', () => {
  const ticket = { ticket_id: 'E-REL-004', description: 'Employees cannot resolve internal hostnames after the DNS change.' }

  it('explains the outcome, lists the backend questions, and keeps the ticket visible', () => {
    render(<AnalysisResult response={clarification} ticket={ticket} />)

    expect(screen.getByText('Clarification needed')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'More information needed' })).toBeTruthy()
    expect(screen.getByText(/no\s+troubleshooting steps were generated/)).toBeTruthy()
    expect(within(section('Message from the agent')).getByText(clarification.summary as string)).toBeTruthy()

    const questions = within(section('Questions from the agent')).getAllByRole('listitem')
    expect(questions.map((item) => item.textContent)).toEqual(clarification.clarifying_questions)

    const yourTicket = section('Your ticket')
    expect(within(yourTicket).getByText(ticket.ticket_id)).toBeTruthy()
    expect(within(yourTicket).getByText(ticket.description)).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Recommended steps' })).toBeNull()
  })

  it('does not invent questions when the backend sends none', () => {
    render(<AnalysisResult response={{ ...clarification, clarifying_questions: [], summary: null }} />)

    const questions = section('Questions from the agent')
    expect(within(questions).queryAllByRole('listitem')).toHaveLength(0)
    expect(within(questions).getByText('The backend returned no specific questions.')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Message from the agent' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Your ticket' })).toBeNull()
  })
})

describe('fallback', () => {
  it('shows the backend message once and keeps rejected passages collapsed and labelled', () => {
    const { container } = render(<AnalysisResult response={fallback} />)

    expect(screen.getByText('Fallback')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'No reliable documented answer' })).toBeTruthy()
    expect(screen.getAllByText(fallback.summary as string)).toHaveLength(1)
    expect(screen.queryByRole('heading', { name: 'Recommended steps' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Evidence' })).toBeNull()

    const rejected = container.querySelector('details') as HTMLDetailsElement
    expect(rejected.open).toBe(false)
    expect(within(rejected).getByText(/Show the 5 passages that were checked and rejected/)).toBeTruthy()
    expect(within(rejected).getByText(/do not support any solution/)).toBeTruthy()
  })

  it('falls back to the recommendation text when summary is missing', () => {
    const message = fallback.recommendations[0]
    render(<AnalysisResult response={{ ...fallback, summary: null }} />)

    expect(within(section('Message from the agent')).getByText(message)).toBeTruthy()
    expect(screen.getAllByText(message)).toHaveLength(1)
  })

  it('handles a fallback with no message, notes, or sources', () => {
    const { container } = render(
      <AnalysisResult response={{ ...fallback, summary: null, recommendations: [], sources: [] }} />,
    )

    expect(screen.getByText('The backend did not include a message.')).toBeTruthy()
    expect(container.querySelector('details')).toBeNull()
  })

  it('lists additional backend notes that differ from the message', () => {
    render(
      <AnalysisResult
        response={{ ...fallback, recommendations: [fallback.summary as string, 'Escalate to the platform team.'] }}
      />,
    )

    const notes = within(section('Additional notes from the agent')).getAllByRole('listitem')
    expect(notes.map((item) => item.textContent)).toEqual(['Escalate to the platform team.'])
  })
})

describe('result actions and metadata', () => {
  it('labels category and ticket ID for assistive technology and calls onReset', async () => {
    const onReset = vi.fn()
    render(<AnalysisResult response={grounded} onReset={onReset} />)

    expect(screen.getByText('Category:')).toBeTruthy()
    expect(screen.getByText('Ticket ID:')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Analyze another ticket' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('omits the reset action when no handler is given', () => {
    render(<AnalysisResult response={grounded} />)
    expect(screen.queryByRole('button', { name: 'Analyze another ticket' })).toBeNull()
  })
})

describe('groupSourcesByDocument', () => {
  it('keeps first-appearance order and the smallest distance per document', () => {
    const groups = groupSourcesByDocument([
      source({ source: 'b.md', distance: 0.4 }),
      source({ source: 'a.md', distance: 0.3, category: 'network' }),
      source({ source: 'b.md', distance: 0.2, category: 'web' }),
    ])

    expect(groups.map((group) => [group.source, group.passages.length, group.bestDistance, group.category])).toEqual([
      ['b.md', 2, 0.2, 'web'],
      ['a.md', 1, 0.3, 'network'],
    ])
  })

  it('names documents with a blank filename instead of dropping them', () => {
    expect(groupSourcesByDocument([source({ source: '  ' })])[0].source).toBe('Unnamed document')
  })
})
