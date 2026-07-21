export interface MetricPoint {
  step: number
  reward_mean?: number
  pass_at_1?: number
  entropy?: number
}

export type EvidenceMetric = 'quality' | 'reward' | 'entropy'
