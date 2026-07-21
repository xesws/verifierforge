import type { Control, Job, Metrics } from './contracts'
import type { MetricPoint } from '../types'

export function metricPoints(metrics: Metrics): MetricPoint[] {
  return metrics.steps.map((step, index) => ({
    step,
    reward_mean: metrics.reward_mean[index],
    pass_at_1: metrics.pass_at_1[index],
    entropy: metrics.entropy[index],
  }))
}

export function controlPoints(metrics: Metrics, control: Control): MetricPoint[] {
  return control.pass_at_1.map((passAt1, index) => ({
    step: metrics.steps[index] ?? index + 1,
    pass_at_1: passAt1,
  }))
}

export function selectedCheckpoint(job: Job): number | null {
  const name = job.endpoint?.model_name ?? ''
  const match = name.match(/step[-_ ]?(\d+)/i)
  if (match) return Number(match[1])
  if (job.job_id === 'd4-m3-1p5b-r1-v0125') return 350
  return null
}
