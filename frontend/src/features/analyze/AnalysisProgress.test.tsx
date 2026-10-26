import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AnalysisProgress } from './AnalysisProgress'

function elapsed(text: string) {
  return screen.getByText((_content, element) => element?.tagName === 'P' && element.textContent === text)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AnalysisProgress', () => {
  it('names the ticket and counts elapsed seconds', () => {
    render(<AnalysisProgress ticketId="E-REL-001" />)

    expect(screen.getByText('E-REL-001')).toBeTruthy()
    expect(elapsed('Elapsed time: 0 s')).toBeTruthy()
    act(() => {
      vi.advanceTimersByTime(5_000)
    })
    expect(elapsed('Elapsed time: 5 s')).toBeTruthy()
  })

  it('announces a slow request only after 30 seconds', () => {
    render(<AnalysisProgress ticketId="E-REL-001" />)
    const notice = screen.getByRole('status')

    act(() => {
      vi.advanceTimersByTime(29_000)
    })
    expect(notice.textContent).toBe('')

    act(() => {
      vi.advanceTimersByTime(1_000)
    })
    expect(notice.textContent).toMatch(/taking longer than usual.*after 90 seconds/)
  })
})
