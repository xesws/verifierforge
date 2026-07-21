import { describe, expect, it } from 'vitest'
import type { DemoTrafficStatus, LivePassRate } from '../api/contracts'
import { advanceGuardianRun, beginGuardianRun } from '../components/guardianRun'

const idle: DemoTrafficStatus = { total: 200, rate: 5, sent: 0, success: 0, failed: 0, running: true, error: null }
const baseline: LivePassRate = { cluster_id: 'data-pull-sql', points: [{ timestamp: '2026-07-21T00:00:00Z', pass_rate: 0.95 }] }

describe('Guardian run projection', () => {
  it('creates one carry-forward chart point for every observed traffic request', () => {
    const started = beginGuardianRun(idle, baseline)
    const firstProgress = advanceGuardianRun(
      started,
      { ...idle, sent: 5, success: 5 },
      { ...baseline, points: [...baseline.points, { timestamp: '2026-07-21T00:00:01Z', pass_rate: 0.9 }] },
    )

    expect(firstProgress.points.map((point) => point.request)).toEqual([0, 1, 2, 3, 4, 5])
    expect(firstProgress.points.map((point) => point.pass_rate)).toEqual([0.95, 0.95, 0.95, 0.95, 0.95, 0.9])
    expect(firstProgress.guardianSamples).toBe(1)

    const completed = advanceGuardianRun(
      firstProgress,
      { ...idle, sent: 200, success: 200, running: false },
      { ...baseline, points: [...baseline.points, { timestamp: '2026-07-21T00:00:01Z', pass_rate: 0.9 }] },
    )
    expect(completed.points.slice(1)).toHaveLength(200)
    expect(completed.points.at(-1)).toEqual({ request: 200, pass_rate: 0.9 })
    expect(completed.running).toBe(false)
  })
})
