import { afterEach, describe, expect, it, vi } from 'vitest'
import { BrowserSqlSandbox } from '../sql/client'

class HangingWorker {
  static instances: HangingWorker[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  terminated = false

  constructor() {
    HangingWorker.instances.push(this)
    queueMicrotask(() => this.onmessage?.({ data: { type: 'ready', sqliteVersion: '3.49.1' } } as MessageEvent))
  }

  postMessage(message: { request: { executionId: string; rawSql: string } }) {
    queueMicrotask(() => this.onmessage?.({
      data: {
        type: 'stage',
        stage: {
          executionId: message.request.executionId,
          phase: 'executing',
          detail: 'SQLite is executing this SQL now.',
          at: '2026-07-20T00:00:00Z',
          executedSql: message.request.rawSql,
        },
      },
    } as MessageEvent))
  }

  terminate() { this.terminated = true }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  HangingWorker.instances = []
})

describe('BrowserSqlSandbox', () => {
  it('terminates a hung query at two seconds and starts a clean worker', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('Worker', HangingWorker)
    const states: string[] = []
    const stages: string[] = []
    const sandbox = new BrowserSqlSandbox((state) => states.push(state))
    await vi.advanceTimersByTimeAsync(0)

    const pending = sandbox.execute('chatcmpl-timeout', 'WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x', (stage) => stages.push(stage.phase))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(2_001)
    const result = await pending

    expect(result.status).toBe('timeout')
    expect(result.error?.code).toBe('query_timeout')
    expect(stages).toEqual(['executing', 'timeout'])
    expect(HangingWorker.instances[0].terminated).toBe(true)
    expect(HangingWorker.instances).toHaveLength(2)
    expect(states).toContain('ready')
    sandbox.dispose()
  })
})
