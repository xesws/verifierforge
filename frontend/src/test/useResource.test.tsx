import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResource } from '../state/useResource'

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useResource polling', () => {
  it('resumes immediately when a hidden tab becomes visible', async () => {
    vi.useFakeTimers()
    let hidden = true
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
    const loader = vi.fn().mockResolvedValue({ state: 'loading' })

    renderHook(() => useResource(loader, [], { pollMs: () => 5_000 }))
    expect(loader).toHaveBeenCalledTimes(1)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(loader).toHaveBeenCalledTimes(2)
  })
})
