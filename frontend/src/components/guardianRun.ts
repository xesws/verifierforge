import type { DemoTrafficStatus, LivePassRate } from '../api/contracts'

export interface GuardianRunPoint {
  request: number
  pass_rate: number | null
}

export interface GuardianRunView {
  total: number
  sent: number
  success: number
  failed: number
  running: boolean
  baselineGuardianCount: number
  guardianSamples: number
  latestPassRate: number | null
  points: GuardianRunPoint[]
}

export function beginGuardianRun(status: DemoTrafficStatus, guardian: LivePassRate | null): GuardianRunView {
  const latestPassRate = latestGuardianRate(guardian)
  return {
    total: status.total,
    sent: status.sent,
    success: status.success,
    failed: status.failed,
    running: status.running,
    baselineGuardianCount: guardian?.points.length ?? 0,
    guardianSamples: 0,
    latestPassRate,
    points: Array.from({ length: status.sent + 1 }, (_, request) => ({ request, pass_rate: latestPassRate })),
  }
}

export function advanceGuardianRun(
  current: GuardianRunView,
  status: DemoTrafficStatus,
  guardian: LivePassRate | null,
): GuardianRunView {
  const sent = Math.max(current.sent, Math.min(status.sent, status.total))
  const points = current.points.slice()
  for (let request = current.sent + 1; request <= sent; request += 1) {
    points.push({ request, pass_rate: current.latestPassRate })
  }

  const observedRate = latestGuardianRate(guardian) ?? current.latestPassRate
  if (observedRate !== current.latestPassRate && points.length > 0) {
    const final = points.length - 1
    points[final] = { ...points[final], pass_rate: observedRate }
  }

  const guardianCount = guardian?.points.length ?? current.baselineGuardianCount
  return {
    ...current,
    total: status.total,
    sent,
    success: status.success,
    failed: status.failed,
    running: status.running,
    guardianSamples: Math.max(0, guardianCount - current.baselineGuardianCount),
    latestPassRate: observedRate,
    points,
  }
}

export function latestGuardianRate(guardian: LivePassRate | null): number | null {
  return guardian?.points.at(-1)?.pass_rate ?? null
}
