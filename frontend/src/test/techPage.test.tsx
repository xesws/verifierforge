import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from '../app/App'

afterEach(() => cleanup())

describe('public technical deep dive', () => {
  it('renders the canonical article without reviewer authentication', async () => {
    window.history.replaceState({}, '', '/tech')
    const { container } = render(<App />)

    await waitFor(() => expect(container.querySelector('h1')?.textContent).toMatch(/turning repetitive LLM spend/i))
    expect(screen.queryByText('Reviewer access')).not.toBeInTheDocument()
    expect(container.querySelector('aside[aria-label="Article contents"] nav')).toBeInTheDocument()
    expect(screen.getByText('58.3% → 78.3%')).toBeInTheDocument()
    expect(container.querySelectorAll('.tech-markdown img')).toHaveLength(6)
    expect(container.querySelector('.katex-display')).toBeInTheDocument()
  })

  it('links back to the reviewer product', async () => {
    window.history.replaceState({}, '', '/tech')
    render(<App />)
    expect(await screen.findByRole('link', { name: /Open the product/i })).toHaveAttribute('href', '/discover')
  })
})
