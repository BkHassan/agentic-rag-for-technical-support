import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  RECORDED_CLARIFICATION_NEEDED,
  RECORDED_FALLBACK,
  RECORDED_GROUNDED_ANSWER,
} from '../../test/recordedResponses'
import { AnalyzePage } from './AnalyzePage'

const DESCRIPTION = 'Users cannot connect to the application because the database connection keeps timing out.'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function stubFetch(implementation: typeof fetch) {
  const fetchMock = vi.fn<typeof fetch>(implementation)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function deferredFetch() {
  let resolve: (response: Response) => void = () => {}
  const fetchMock = stubFetch(
    () =>
      new Promise<Response>((settle) => {
        resolve = settle
      }),
  )
  return { fetchMock, respond: (response: Response) => resolve(response) }
}

async function fillTicket(ticketId: string, description: string) {
  const user = userEvent.setup()
  render(<AnalyzePage />)
  const idInput = screen.getByRole('textbox', { name: /ticket id/i })
  await user.clear(idInput)
  await user.type(idInput, ticketId)
  const descriptionField = screen.getByRole('textbox', { name: /issue description/i })
  await user.click(descriptionField)
  // Clipboard paste of whitespace-only text is a no-op in jsdom; type those cases.
  if (description.trim() === '') {
    await user.type(descriptionField, description)
  } else {
    await user.paste(description)
  }
  return user
}

function requestBody(fetchMock: ReturnType<typeof stubFetch>, call = 0): unknown {
  return JSON.parse(String(fetchMock.mock.calls[call][1]?.body))
}

afterEach(cleanup)

describe('ticket form', () => {
  it('blocks a blank description without calling the API', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(200, RECORDED_GROUNDED_ANSWER)))
    const user = await fillTicket('E-REL-001', '   ')

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText('Describe the issue before analyzing.')).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /issue description/i }))
  })

  it('submits the trimmed ticket once, ignores repeat submits while loading, and shows the result', async () => {
    const { fetchMock, respond } = deferredFetch()
    const user = await fillTicket('E-REL-001', `  ${DESCRIPTION}  `)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))

    const busyButton = screen.getByRole('button', { name: /analyzing/i }) as HTMLButtonElement
    expect(busyButton.disabled).toBe(true)
    await user.click(busyButton)
    const description = screen.getByRole('textbox', { name: /issue description/i })
    description.focus()
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/tickets/analyze')
    expect(requestBody(fetchMock)).toEqual({ ticket_id: 'E-REL-001', description: DESCRIPTION })

    respond(jsonResponse(200, RECORDED_GROUNDED_ANSWER))

    expect(await screen.findByText('Recommended steps')).toBeTruthy()
    expect(screen.getByText(RECORDED_GROUNDED_ANSWER.recommendations[0])).toBeTruthy()
    expect(screen.getByText(RECORDED_GROUNDED_ANSWER.sources[0].source)).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Analyze ticket' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('keeps the input after an HTTP error, hides backend detail, and retries', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(200, RECORDED_CLARIFICATION_NEEDED)))
    fetchMock.mockResolvedValueOnce(
      jsonResponse(503, { detail: 'Upstream Gemini service error: overloaded due to high demand.' }),
    )
    const user = await fillTicket('E-REL-004', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('Service unavailable')).toBeTruthy()
    expect(alert.textContent).not.toContain('overloaded')
    expect((screen.getByRole('textbox', { name: /issue description/i }) as HTMLTextAreaElement).value).toBe(
      DESCRIPTION,
    )
    expect((screen.getByRole('textbox', { name: /ticket id/i }) as HTMLInputElement).value).toBe('E-REL-004')

    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Questions from the agent')).toBeTruthy()
    expect(screen.getByText(RECORDED_CLARIFICATION_NEEDED.clarifying_questions[0])).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(requestBody(fetchMock, 1)).toEqual(requestBody(fetchMock, 0))
  })

  it('reports an unreachable backend and keeps the input', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    const user = await fillTicket('E-REL-001', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('Backend unreachable')).toBeTruthy()
    expect((screen.getByRole('textbox', { name: /issue description/i }) as HTMLTextAreaElement).value).toBe(
      DESCRIPTION,
    )
  })

  it('shows the submitted ticket next to a clarification result', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, RECORDED_CLARIFICATION_NEEDED)))
    const user = await fillTicket('E-REL-004', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))

    const heading = await screen.findByRole('heading', { name: 'Your ticket' })
    const yourTicket = heading.closest('section') as HTMLElement
    expect(within(yourTicket).getByText('E-REL-004')).toBeTruthy()
    expect(within(yourTicket).getByText(DESCRIPTION)).toBeTruthy()
  })

  it('"Analyze another ticket" clears the result and starts a fresh ticket', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(200, RECORDED_FALLBACK)))
    const user = await fillTicket('E-IRR-001', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))
    await user.click(await screen.findByRole('button', { name: 'Analyze another ticket' }))

    expect(screen.getByText('No ticket analyzed yet')).toBeTruthy()
    expect(screen.queryByText('No reliable documented answer')).toBeNull()
    const description = screen.getByRole('textbox', { name: /issue description/i }) as HTMLTextAreaElement
    expect(description.value).toBe('')
    expect(document.activeElement).toBe(description)
    const ticketId = (screen.getByRole('textbox', { name: /ticket id/i }) as HTMLInputElement).value
    expect(ticketId).toMatch(/^WEB-\d{8}-[A-Z2-9]{4}$/)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('"Clear" also removes the result for the cleared ticket', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, RECORDED_GROUNDED_ANSWER)))
    const user = await fillTicket('E-REL-001', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))
    expect(await screen.findByText('Recommended steps')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    expect(screen.queryByText('Recommended steps')).toBeNull()
    expect(screen.getByText('No ticket analyzed yet')).toBeTruthy()
    expect((screen.getByRole('textbox', { name: /issue description/i }) as HTMLTextAreaElement).value).toBe('')
  })

  it('"Clear" also removes an error for the cleared ticket', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    const user = await fillTicket('E-REL-001', DESCRIPTION)

    await user.click(screen.getByRole('button', { name: 'Analyze ticket' }))
    expect(await screen.findByRole('alert')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('No ticket analyzed yet')).toBeTruthy()
  })
})
